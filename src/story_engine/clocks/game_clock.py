from datetime import datetime, timedelta

class GameClock:
    def __init__(self, start_time: datetime = None, step_duration: timedelta = None):
        self.current_step = 0
        self.start_time = start_time or datetime.now()
        self.step_duration = step_duration or timedelta(minutes=1)
        self.current_time = self.start_time

    def tick(self):
        self.current_step += 1
        self.current_time += self.step_duration

    def advance_to(self, step: int):
        target = max(self.current_step, int(step))
        delta = target - self.current_step
        if delta:
            self.current_step = target
            self.current_time += self.step_duration * delta

    def get_time_display(self) -> str:
        return self.current_time.strftime("%Y-%m-%d %H:%M:%S")
    
    def now(self) -> datetime:
        return self.current_time
