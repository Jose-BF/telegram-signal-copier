import io

from pipeline_progress import ProgressReporter, render_progress


def test_render_progress_has_stable_width_and_clamps_values():
    assert render_progress(3, 10, "Simulando", width=10) == (
        "[3/10] [###-------] Simulando"
    )
    assert render_progress(15, 10, "Listo", width=10) == (
        "[10/10] [##########] Listo"
    )


def test_render_progress_handles_empty_total():
    assert render_progress(0, 0, "Sin trabajo", width=5) == (
        "[0/0] [-----] Sin trabajo"
    )


def test_reporter_throttles_intermediate_updates_but_always_completes():
    stream = io.StringIO()
    times = iter([100.0, 100.1, 100.2])
    reporter = ProgressReporter(
        stream=stream,
        min_interval_s=10.0,
        width=5,
        clock=lambda: next(times),
        interactive=False,
    )

    reporter.update(1, 4, "Fase")
    reporter.update(2, 4, "Fase")
    reporter.complete(4, 4, "Fase lista")

    assert stream.getvalue().splitlines() == [
        "[1/4] [#----] Fase",
        "[4/4] [#####] Fase lista | 0.2s",
    ]
