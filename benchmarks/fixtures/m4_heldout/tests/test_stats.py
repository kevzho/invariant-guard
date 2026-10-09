from heldout.stats import mean_of, running_total


def test_mean_of():
    assert mean_of([1.0, 2.0, 3.0]) == 2.0


def test_running_total():
    assert running_total([1, 2, 3]) == [1, 3, 6]
