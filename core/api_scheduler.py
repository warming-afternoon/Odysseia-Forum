import asyncio
import logging
from typing import Coroutine, Any, NamedTuple

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
    coro: Coroutine[Any, Any, Any]
    future: asyncio.Future

class APIScheduler:
    """
    一个带优先级的中央API请求调度器。
    它确保高优先级任务（如用户UI交互）能抢占低优先级任务（如后台索引），
    并使用Semaphore来保证总并发数不超过Discord的速率限制。
    """
    def __init__(self, concurrent_requests: int = 40):
        """
        初始化调度器。
        :param concurrent_requests: 允许同时发往Discord API的最大并发请求数。
        """
        self._queue = asyncio.PriorityQueue[APIRequest]()
        self._semaphore = asyncio.Semaphore(concurrent_requests)
        self._task: asyncio.Task | None = None
        self._is_running = False

    async def _dispatcher_loop(self):
        """调度器的主循环，在后台任务中运行。"""
        logger.info("API acheduler loop started.")
        while self._is_running:
            try:
                # 从优先级队列中获取下一个最高优先级的请求
                request = await self._queue.get()
                
                # 等待一个可用的API令牌
                await self._semaphore.acquire()
                
                # 创建一个任务来执行API调用，这样我们可以附加一个完成回调
                api_task = asyncio.create_task(request.coro)
                api_task.add_done_callback(
                    lambda t: self._on_api_task_done(t, request)
                )

            except asyncio.CancelledError:
                logger.info("API scheduler loop cancelled.")
                break
            except Exception:
                logger.exception("Error in API scheduler loop. This should not happen.")
                # 即使出现意外错误，也短暂等待后继续，以保证调度器健壮性
                await asyncio.sleep(1)

    def _on_api_task_done(self, task: asyncio.Task, request: APIRequest):
        """当API协程执行完毕后的回调函数。"""
        # 总是释放令牌，无论成功还是失败
        self._semaphore.release()
        
        # 将结果或异常设置到future中，以唤醒原始的调用者
        if task.cancelled():
            request.future.cancel()
        elif exc := task.exception():
            request.future.set_exception(exc)
        else:
            request.future.set_result(task.result())
            
        self._queue.task_done()

    async def submit(self, coro: Coroutine, priority: int) -> Any:
        """
        向调度器提交一个API请求。
        这是外部代码与调度器交互的唯一入口。
        
        :param coro: 要执行的API调用协程。
        :param priority: 请求的优先级 (1=最高, 10=低)。
        :return: API调用协程的返回结果。
        """
        if not self._is_running:
            raise RuntimeError("APIScheduler is not running.")
            
        future = asyncio.get_running_loop().create_future()
        request = APIRequest(priority=priority, coro=coro, future=future)
        await self._queue.put(request)
        
        # 等待future被设置结果，并返回
        return await future

    def start(self):
        """启动调度器后台任务。"""
        if self._is_running:
            return
        self._is_running = True
        self._task = asyncio.create_task(self._dispatcher_loop())
        logger.info("APIScheduler started.")

    async def stop(self):
        """优雅地停止调度器。"""
        if not self._is_running or not self._task:
            return
        
        logger.info("Stopping APIScheduler...")
        self._is_running = False
        
        # 等待队列中所有任务完成
        await self._queue.join()
        
        # 取消调度器主循环任务
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        
        logger.info("APIScheduler stopped.")
