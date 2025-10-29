import concurrent.futures
import os

MAX_DEST_THREADS = min(8, (os.cpu_count() or 4) * 2)

class ThreadPools:
    def __init__(self):
        self.dest_pool = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_DEST_THREADS)

    def shutdown(self):
        self.dest_pool.shutdown(wait=False, cancel_futures=True)
