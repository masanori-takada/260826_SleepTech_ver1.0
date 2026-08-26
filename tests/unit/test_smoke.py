"""パッケージが import できることの確認。"""


def test_package_importable():
    import sleeptech

    assert sleeptech is not None
