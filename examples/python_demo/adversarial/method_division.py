class Scaler:
    def __init__(self, k):
        self.k = k

    def scale(self, x):
        return x / self.k
