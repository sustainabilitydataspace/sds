class DummyLogger:
    def bind(self, **kwargs):
        return self

    def info(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


def get_logger(*args, **kwargs):
    return DummyLogger()
