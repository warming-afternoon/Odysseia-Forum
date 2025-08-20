import asyncio
import logging
from itertools import count
from typing import Any, Coroutine, NamedTuple

# 设置日志记录器
logger = logging.getLogger(__name__)


class APIRequest(NamedTuple):
    """
    定义一个API请求的结构，用于在优先级队列中传递。
    - priority: 优先级，数字越小越高。
    - coro: 需要被执行的协程对象 (例如 interaction.response.send_message(...))。
    - future: 一个asyncio.Future对象，用于在协程执行完毕后返回结果或异常。
    """

    priority: int
    count: int
    coro: Coroutine[Any, Any, Any]
    future: asyncio.Future


class APIScheduler:
    """
    一个带优先级的中央API请求调度器。
    它确保高优先级任务（如用户UI交互）能抢占低优先级任务（如后台索引），
    并使用Semaphore来使总并发数不超过Discord的速率限制。
    """

    def __init__(self, concurrent_requests: int = 10):
        """
        初始化调度器。
        :param concurrent_requests: 允许同时发往Discord API的最大并发请求数。
        """
        self._queue = asyncio.PriorityQueue[APIRequest]()
        self._semaphore = asyncio.Semaphore(concurrent_requests)
        self._task: asyncio.Task | None = None
        self._is_running = False
        self._counter = count()

    async def _dispatcher_loop(self):
        """调度器的主循环，从队列中拉取请求并派发给worker。"""
        logger.info("API scheduler loop started.")
        while self._is_running:
            await self._semaphore.acquire()
            try:
                # 从优先级队列中获取下一个最高优先级的请求
                request = await self._queue.get()

                # 检查是否是哨兵对象 (None)
                if request is None:
                    self._semaphore.release()  # 释放为哨兵对象获取的信号量
                    self._queue.task_done()
                    break

                # 为请求创建一个worker任务
                asyncio.create_task(self._worker(request))

                # 立即标记任务完成，因为我们已经把它移交给了worker
                self._queue.task_done()

            except asyncio.CancelledError:
                # 如果在获取信号量后、创建worker前被取消，释放信号量以防泄漏
                self._semaphore.release()
                logger.info("API scheduler loop was explicitly cancelled.")
                break
            except Exception:
                # 同样，在其他异常情况下也要释放信号量
                self._semaphore.release()
                logger.exception("Error in API scheduler loop. This should not happen.")
                # 短暂休眠以避免在持续错误的情况下快速消耗CPU
                await asyncio.sleep(1)

    async def _worker(self, request: APIRequest):
        """
        处理单个API请求的完整生命周期
        """
        max_retries = 3
        retry_delay = 1.0  # 初始延迟时间 (秒)

        for attempt in range(max_retries):
            try:
                # 执行API调用协程
                result = await request.coro
                
                # 成功后，设置结果并退出循环
                if not request.future.done():
                    request.future.set_result(result)

            except asyncio.TimeoutError as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        f"协程 (优先级: {request.priority}) 超时，将在 {retry_delay:.1f} 秒后进行重试 "
                        f"({attempt + 1}/{max_retries-1})..."
                    )
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # 指数退避
                else:
                    # 最后一次重试仍然失败，记录并抛出异常
                    logger.error(
                        f"协程 (优先级: {request.priority}) 在 {max_retries} 次尝试后仍然超时。"
                    )
                    if not request.future.done():
                        request.future.set_exception(e)
            
            except Exception as e:
                # 对于其他异常，立即记录并抛出
                logger.exception(f"执行协程 (优先级: {request.priority}) 时发生错误: {e}")
                if not request.future.done():
                    request.future.set_exception(e)
            finally:
            # 确保信号量总是被释放，无论成功还是失败
                self._semaphore.release()
                return

    async def submit(self, coro: Coroutine, priority: int) -> Any:
        """
        向调度器提交一个API请求。
        这是外部代码与调度器交互的唯一入口。

        :param coro: 要执行的API调用协程。
        :param priority: 请求的优先级 (1=最高, 10=低)。
        :return: API调用协程的返回结果。
        """
        if not self._is_running:
            raise RuntimeError("API 调度器没有在运行")

        future = asyncio.get_running_loop().create_future()
        count = next(self._counter)
        request = APIRequest(priority=priority, count=count, coro=coro, future=future)
        await self._queue.put(request)

        # 等待future被设置结果，并返回
        return await future

    def start(self):
        """启动调度器后台任务。"""
        if self._is_running:
            return
        self._is_running = True
        self._task = asyncio.create_task(self._dispatcher_loop())
        # logger.info("API 调度器开始")

    async def stop(self):
        """停止调度器"""
        if not self._is_running or not self._task:
            return

        logger.info("即将停止API调度器...")
        self._is_running = False

        # 向队列中放入哨兵对象，以通知循环退出
        await self._queue.put(None)  # type: ignore

        # 等待调度器主循环任务自然结束
        if self._task:
            await self._task

        logger.info("API调度器停止")
