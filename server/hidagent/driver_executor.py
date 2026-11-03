"""One native device task at a time. Models, if used, belong to the driver."""
import asyncio

from .drivers import execute_goal, receipt


class DriverExecutor:
    def __init__(self, driver, on_finish=None):
        self.driver = driver
        self.on_finish = on_finish
        self.task = None
        self.owner = None
        self.call_id = None
        self.stopping = False
        self.result = receipt('idle')

    @property
    def busy(self):
        return self.task is not None and not self.task.done()

    def snapshot(self):
        if self.busy:
            return {**self.result, 'status': 'stopping' if self.stopping else 'in_progress',
                    'running': True, 'terminal': False, 'poll_again': True,
                    'input_completion_pending': self.stopping}
        return dict(self.result)

    def start(self, goal, owner, task_id, *, call_id=None):
        if self.busy:
            return {**self.snapshot(), 'status': 'busy'}
        self.owner = owner
        self.call_id = call_id
        self.stopping = False
        self.result = receipt('started', running=True, terminal=False, poll_again=True, task_id=task_id)
        self.task = asyncio.create_task(self._run(goal, task_id))
        return dict(self.result)

    async def _run(self, goal, task_id):
        try:
            if self.stopping:
                result = receipt('stopped')
            else:
                command = getattr(self.driver, 'command', None)
                result = await command(goal) if command else await execute_goal(self.driver, goal)
        except Exception as error:
            result = receipt('uncertain', never_retry_typing=True, reason=type(error).__name__)
        self.result = {**result, 'task_id': task_id}
        if self.stopping:
            self.result.update(status='stopped', running=False, terminal=True, poll_again=False,
                               never_retry_typing=True, input_completion_pending=False)
        if self.on_finish:
            self.on_finish(task_id, self.result)

    def stop(self):
        if self.busy:
            self.stopping = True
            stop = getattr(self.driver, 'request_stop', None)
            if stop:
                stop()
            # Do not cancel a to_thread operation and falsely release its single-owner lease.
            return self.snapshot()
        return {**self.snapshot(), 'status': 'stopped', 'terminal': True}
