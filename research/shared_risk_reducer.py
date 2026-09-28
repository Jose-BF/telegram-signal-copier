"""Exact bounded-memory reduction of shared post-event quote risk."""

from __future__ import annotations

from research.dubai_iterative.shared_replay import (
    SharedReplayResult, SharedRiskFrame, SharedRiskPoint,
)


class SharedRiskOnlineReducer:
    def __init__(self):
        self.point_count = 0
        self.frame_count = 0
        self.previous_index = -1
        self.scopes = None
        self.previous_time = None
        self.last_settled = {}
        self.carried = {}
        self.issues = []
        self.peak = 0
        self.minimum = 0
        self.drawdown = 0
        self.known_count = 0
        self.final_known = None
        self.final_known_index = None
        self.final_known_time = None
        self.last_total = None

    def record_point(self, point: SharedRiskPoint):
        if not isinstance(point, SharedRiskPoint):
            raise ValueError("typed shared risk point required")
        self.point_count += 1
        if point.phase == "settled":
            key = (point.channel, point.signal_id)
            if key in self.carried and self.carried[key] != point:
                raise ValueError("shared grid resumed after carried flat state")
            self.last_settled[key] = point

    def record_frame(self, frame: SharedRiskFrame):
        if not isinstance(frame, SharedRiskFrame):
            raise ValueError("typed shared risk frame required")
        scopes = tuple((channel, identity) for channel, identity, _ in frame.states)
        if self.scopes is None:
            self.scopes = scopes
        if (scopes != self.scopes or frame.tick_index <= self.previous_index
                or self.previous_time is not None and frame.time_ns < self.previous_time):
            raise ValueError("shared grid identity or chronology mismatch")
        if frame.tick_index != self.previous_index + 1:
            self.issues.append("incomplete_post_event_grid")
        self.previous_index = frame.tick_index
        self.previous_time = frame.time_ns
        self.issues.extend(frame.blockers)
        values = []
        for channel, identity, point in frame.states:
            key = (channel, identity)
            if point is not None:
                if ((point.channel, point.signal_id) != key
                        or point.phase != "settled"
                        or point.tick_index > frame.tick_index
                        or point.time_ns > frame.time_ns
                        or point.tick_index == frame.tick_index
                        and point.time_ns != frame.time_ns
                        or self.last_settled.get(key) != point):
                    raise ValueError("shared grid state is not a settled contribution")
                if point.tick_index < frame.tick_index:
                    if (point.positions or point.floating_minor != 0
                            or point.realized_minor is None):
                        raise ValueError("shared grid carried state is not flat")
                    self.carried[key] = point
            total = point.equity_minor if point is not None else None
            if total is None:
                self.issues.append(f"unknown_risk:{identity}")
            values.append(total)
        self.last_total = sum(values) if all(value is not None for value in values) else None
        if self.last_total is not None:
            self.peak = max(self.peak, self.last_total)
            self.minimum = min(self.minimum, self.last_total)
            self.drawdown = max(self.drawdown, self.peak - self.last_total)
            self.known_count += 1
            self.final_known = self.last_total
            self.final_known_index = frame.tick_index
            self.final_known_time = frame.time_ns
        self.frame_count += 1

    def finalize(self, report: SharedReplayResult):
        if (not isinstance(report, SharedReplayResult) or not report.risk_streamed
                or report.risk or report.risk_grid
                or report.risk_point_count != self.point_count
                or report.risk_grid_count != self.frame_count):
            raise ValueError("streamed risk report and reducer counts differ")
        scopes = tuple((channel, identity) for channel, identity, _ in report.baskets)
        if not scopes or len(set(scopes)) != len(scopes) or scopes != self.scopes:
            raise ValueError("shared grid scope identity mismatch")
        finished = {(channel, identity): result
                    for channel, identity, result in report.baskets}
        for key, point in self.carried.items():
            result = finished[key]
            if (result is None or result.blockers or result.pnl_eur is None
                    or result.last_tick_index > point.tick_index
                    or result.pnl_eur * 10 ** report.currency_digits != point.realized_minor):
                raise ValueError("shared grid carried state is not a finished basket")
        issues = list(report.blockers) + self.issues
        if self.frame_count != report.expected_quote_count:
            issues.append("incomplete_post_event_grid")
        known = None
        if self.known_count:
            known = {"minimum_from_origin": self.minimum,
                     "maximum_from_origin": self.peak,
                     "max_drawdown": self.drawdown,
                     "final_net": self.final_known,
                     "known_samples": self.known_count,
                     "final_tick_index": self.final_known_index,
                     "final_time_ns": self.final_known_time}
        return {"grid_contract": "post_event_quote_grid_v1",
                "provenance": "modelled", "scope": "selected_initially_flat_baskets",
                "expected_scopes": list(scopes),
                "currency": report.profile.account_currency,
                "currency_digits": report.currency_digits, "units": "account_currency_minor",
                "origin": 0, "metrics": known if not issues else None,
                "known_sample_metrics": known,
                "final_total_minor": (self.last_total
                                      if self.previous_index == report.expected_quote_count - 1 else None),
                "sample_count": self.frame_count, "samples_retained": False,
                "blockers": tuple(dict.fromkeys(issues)),
                "native_parity_verified": False, "account_equity_verified": False}
