"""
商品分析分发器
将卖家资料采集、图片下载、AI 分析和结果保存移出主抓取链路。
"""
import asyncio
import copy
import os
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from src.keyword_rule_engine import build_search_text, evaluate_keyword_rules


SellerLoader = Callable[[str], Awaitable[dict]]
ImageDownloader = Callable[[str, list[str], str], Awaitable[list[str]]]
AIAnalyzer = Callable[[dict, list[str], str], Awaitable[Optional[dict]]]
Notifier = Callable[[dict, str], Awaitable[None]]
Saver = Callable[[dict, str], Awaitable[bool]]


class AIAnalysisAbortError(RuntimeError):
    """Raised when consecutive AI analysis failures exceed threshold."""


@dataclass(frozen=True)
class ItemAnalysisJob:
    keyword: str
    task_name: str
    decision_mode: str
    analyze_images: bool
    prompt_text: str
    keyword_rules: tuple[str, ...]
    final_record: dict
    seller_id: Optional[str]
    zhima_credit_text: Optional[str]
    registration_duration_text: str


class ItemAnalysisDispatcher:
    """用受控并发处理商品分析和落盘。"""

    def __init__(
        self,
        *,
        concurrency: int,
        skip_ai_analysis: bool,
        seller_loader: SellerLoader,
        image_downloader: ImageDownloader,
        ai_analyzer: AIAnalyzer,
        notifier: Notifier,
        saver: Saver,
        max_consecutive_ai_failures: int = 3,
    ) -> None:
        self._semaphore = asyncio.Semaphore(max(1, concurrency))
        self._skip_ai_analysis = skip_ai_analysis
        self._seller_loader = seller_loader
        self._image_downloader = image_downloader
        self._ai_analyzer = ai_analyzer
        self._notifier = notifier
        self._saver = saver
        self._max_consecutive_ai_failures = max(1, int(max_consecutive_ai_failures))
        self._consecutive_ai_failures = 0
        self._tasks: set[asyncio.Task] = set()
        self._fatal_error: Exception | None = None
        self.completed_count = 0

    def submit(self, job: ItemAnalysisJob) -> None:
        if self._fatal_error is not None:
            raise self._fatal_error
        task = asyncio.create_task(self._process_with_limit(job))
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        # 防止 "Task exception was never retrieved"。
        # 真正的错误在 join() 里统一抛出。
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None and self._fatal_error is None:
            self._fatal_error = exc

    def ensure_healthy(self) -> None:
        if self._fatal_error is not None:
            raise self._fatal_error

    async def join(self) -> None:
        while self._tasks:
            batch = tuple(self._tasks)
            await asyncio.gather(*batch, return_exceptions=True)

        if self._fatal_error is not None:
            error = self._fatal_error
            self._fatal_error = None
            raise error

    async def _process_with_limit(self, job: ItemAnalysisJob) -> None:
        async with self._semaphore:
            try:
                await self._process_job(job)
            except AIAnalysisAbortError as exc:
                if self._fatal_error is None:
                    self._fatal_error = exc
                # 终止阈值触发后，取消其余未完成任务，缩短收敛时间。
                for task in tuple(self._tasks):
                    if task is not asyncio.current_task() and not task.done():
                        task.cancel()
                raise

    async def _process_job(self, job: ItemAnalysisJob) -> None:
        record = copy.deepcopy(job.final_record)
        item_data = record.get("商品信息", {}) or {}
        record["卖家信息"] = await self._load_seller_info(job)
        record["ai_analysis"] = await self._build_analysis_result(job, record)

        if job.decision_mode == "ai" and not record["ai_analysis"].get("_analysis_ok", False):
            self._consecutive_ai_failures += 1
            print(
                f"   [AI分析] 连续失败 {self._consecutive_ai_failures}/{self._max_consecutive_ai_failures}，跳过保存该条结果。"
            )
            if self._consecutive_ai_failures >= self._max_consecutive_ai_failures:
                raise AIAnalysisAbortError(
                    f"AI分析连续失败 {self._consecutive_ai_failures} 次，终止任务。"
                )
            return

        self._consecutive_ai_failures = 0
        record["ai_analysis"].pop("_analysis_ok", None)

        if await self._saver(record, job.keyword):
            self.completed_count += 1
        await self._notify_if_recommended(item_data, record["ai_analysis"])

    async def _load_seller_info(self, job: ItemAnalysisJob) -> dict:
        seller_info = {}
        if job.seller_id:
            try:
                seller_info = await self._seller_loader(job.seller_id)
            except Exception as exc:
                print(f"   [卖家] 采集卖家 {job.seller_id} 信息失败: {exc}")
        merged = copy.deepcopy(seller_info or {})
        merged["卖家芝麻信用"] = job.zhima_credit_text
        merged["卖家注册时长"] = job.registration_duration_text
        return merged

    async def _build_analysis_result(self, job: ItemAnalysisJob, record: dict) -> dict:
        if job.decision_mode == "keyword":
            return self._build_keyword_result(job, record)
        if self._skip_ai_analysis:
            return self._build_skip_ai_result()
        return await self._run_ai_analysis(job, record)

    def _build_keyword_result(self, job: ItemAnalysisJob, record: dict) -> dict:
        search_text = build_search_text(record)
        result = evaluate_keyword_rules(list(job.keyword_rules), search_text)
        result["_analysis_ok"] = True
        return result

    def _build_skip_ai_result(self) -> dict:
        return {
            "analysis_source": "ai",
            "is_recommended": True,
            "reason": "商品已跳过AI分析，直接通知",
            "keyword_hit_count": 0,
            "_analysis_ok": True,
        }

    def _build_ai_error_result(self, reason: str, *, error: str = "") -> dict:
        payload = {
            "analysis_source": "ai",
            "is_recommended": False,
            "reason": reason,
            "keyword_hit_count": 0,
            "_analysis_ok": False,
        }
        if error:
            payload["error"] = error
        return payload

    async def _run_ai_analysis(self, job: ItemAnalysisJob, record: dict) -> dict:
        image_paths: list[str] = []
        try:
            image_paths = await self._download_images(job, record)
            if not job.prompt_text:
                return self._build_ai_error_result("任务未配置AI prompt，跳过分析。")
            ai_result = await self._ai_analyzer(record, image_paths, job.prompt_text)
            if not ai_result:
                return self._build_ai_error_result(
                    "AI analysis returned None after retries.",
                    error="AI analysis returned None after retries.",
                )
            ai_result.setdefault("analysis_source", "ai")
            ai_result.setdefault("keyword_hit_count", 0)
            ai_result["_analysis_ok"] = True
            return ai_result
        except Exception as exc:
            return self._build_ai_error_result(
                f"AI分析异常: {exc}",
                error=str(exc),
            )
        finally:
            self._cleanup_images(image_paths)

    async def _download_images(self, job: ItemAnalysisJob, record: dict) -> list[str]:
        if not job.analyze_images:
            return []
        item_data = record.get("商品信息", {}) or {}
        image_urls = item_data.get("商品图片列表", [])
        if not image_urls:
            return []
        return await self._image_downloader(
            item_data["商品ID"],
            image_urls,
            job.task_name,
        )

    def _cleanup_images(self, image_paths: list[str]) -> None:
        for img_path in image_paths:
            try:
                if os.path.exists(img_path):
                    os.remove(img_path)
            except Exception as exc:
                print(f"   [图片] 删除图片文件时出错: {exc}")

    async def _notify_if_recommended(self, item_data: dict, analysis_result: dict) -> None:
        if not analysis_result.get("is_recommended"):
            return
        try:
            await self._notifier(item_data, analysis_result.get("reason", "无"))
        except Exception as exc:
            print(f"   [通知] 发送推荐通知失败: {exc}")
