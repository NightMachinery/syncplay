SHARED_PLAYBACK_SPEED = "sharedPlaybackSpeed"
MIN_SPEED = 0.25
MAX_SPEED = 4.0


def validRoomSpeed(value):
    return type(value) in (int, float) and MIN_SPEED <= value <= MAX_SPEED


def validPlayerSpeed(value):
    # mpv can be configured outside the shared range, and drift correction can
    # temporarily take it below the minimum room speed.
    return type(value) in (int, float) and 0 < value < float("inf")


def validSpeedRevision(value):
    return type(value) is int and value >= 0
