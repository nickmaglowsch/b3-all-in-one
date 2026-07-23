"""Downsampling behind /portfolio/price-history. Pure — no DB, no fixtures.

    pytest tests/test_sparkline.py     (or: python tests/test_sparkline.py)
"""
from platform_app.routes import _downsample


def test_downsample():
    # Short series pass through untouched — a 6-week-old ticker keeps its shape.
    short = [1.0, 2.0, 3.0]
    assert _downsample(short) is short

    # ~6 months of daily closes → a payload a table row can carry.
    daily = [float(i) for i in range(126)]
    out = _downsample(daily)
    assert 30 <= len(out) <= 50, len(out)

    # Endpoints survive: the sparkline's colour and % label read first vs last.
    assert out[0] == 0.0 and out[-1] == 125.0
    assert out == sorted(out)  # chronological order preserved

    # Worst case for the "keep the last point" fixup: len-1 lands off the step
    # grid (250 // 7 → last kept index 245, so 249 must be appended).
    long = [float(i) for i in range(250)]
    assert _downsample(long)[-1] == 249.0

    # …and where it lands exactly on it (121 → step 4, 120 % 4 == 0): the last
    # point comes from the stride, so it must not be appended a second time.
    exact = _downsample([float(i) for i in range(121)])
    assert exact[-1] == 120.0 and exact[-2] == 116.0


if __name__ == "__main__":
    test_downsample()
    print("ok")
