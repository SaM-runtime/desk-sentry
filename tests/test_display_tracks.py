from itertools import combinations

import pytest

from desk_sentry.engine import TrackView, display_tracks
from desk_sentry.sentry import Role


def view(
    track_id: int,
    role: Role,
    *,
    box: tuple[float, float, float, float] = (0.1, 0.1, 0.5, 0.9),
    score: float = 0.5,
    name: str | None = None,
) -> TrackView:
    return TrackView(track_id, box, role, score, name)


def test_overlapping_non_self_tracks_follow_every_priority_tier() -> None:
    tiers = (
        view(1, Role.APPROACHING),
        view(2, Role.INTRUDER),
        view(3, Role.FAR, name="Ada"),
        view(4, Role.KNOWN),
        view(5, Role.STILL),
        view(6, Role.PASSING),
        view(7, Role.PENDING),
        view(8, Role.FAR),
    )

    for preferred, lower in combinations(tiers, 2):
        lower = view(lower.track_id, lower.role, score=0.99, name=lower.name)
        assert display_tracks((lower, preferred)) == (preferred,)
        assert display_tracks((preferred, lower)) == (preferred,)


def test_equal_priority_keeps_higher_score() -> None:
    low = view(1, Role.PENDING, score=0.4)
    high = view(2, Role.PENDING, score=0.8)

    assert display_tracks((low, high)) == (high,)


def test_overlap_is_not_chained_across_two_people() -> None:
    # A duplicate box bridging two people must not merge the two people.
    left = view(1, Role.PENDING, box=(0.0, 0.0, 0.5, 1.0))
    middle = view(2, Role.PASSING, box=(0.2, 0.0, 0.7, 1.0))
    right = view(3, Role.INTRUDER, box=(0.4, 0.0, 0.9, 1.0))

    assert display_tracks((left, middle, right)) == (left, right)


@pytest.mark.parametrize(
    "role",
    (Role.PENDING, Role.STILL, Role.PASSING, Role.FAR, Role.KNOWN),
)
def test_non_alert_track_overlapping_self_is_hidden(role: Role) -> None:
    self_track = view(1, Role.SELF)
    other = view(2, role)

    assert display_tracks((self_track, other)) == (self_track,)


def test_labelled_person_overlapping_self_is_retained() -> None:
    self_track = view(1, Role.SELF)
    known = view(2, Role.KNOWN, name="Ada")

    assert display_tracks((self_track, known)) == (self_track, known)


@pytest.mark.parametrize("role", (Role.INTRUDER, Role.APPROACHING))
def test_alert_track_overlapping_self_is_retained(role: Role) -> None:
    self_track = view(1, Role.SELF)
    alert = view(2, role)

    assert display_tracks((self_track, alert)) == (self_track, alert)


def test_disjoint_people_are_all_retained_in_input_order() -> None:
    left = view(1, Role.PENDING, box=(0.0, 0.0, 0.2, 0.8))
    right = view(2, Role.INTRUDER, box=(0.7, 0.0, 0.9, 0.8))

    assert display_tracks((left, right)) == (left, right)


def test_display_tracks_does_not_mutate_input_or_views() -> None:
    original = [view(1, Role.PENDING), view(2, Role.INTRUDER)]
    before = list(original)

    display_tracks(original)

    assert original == before
    assert original[0] is before[0]
    assert original[1] is before[1]


@pytest.mark.parametrize(
    ("role", "seen", "visible"),
    [
        (Role.PENDING, False, False),
        (Role.PENDING, True, True),
        (Role.INTRUDER, False, True),
        (Role.APPROACHING, False, True),
        (Role.STILL, False, True),
        (Role.PASSING, False, True),
        (Role.FAR, False, True),
        (Role.KNOWN, False, True),
    ],
)
def test_only_unseen_pending_cards_are_hidden(role: Role, seen: bool, visible: bool) -> None:
    track = TrackView(1, (0.0, 0.0, 0.3, 0.9), role, 0.8, seen=seen)
    assert display_tracks((track,)) == ((track,) if visible else ())


def test_hidden_named_pending_cannot_suppress_a_visible_track() -> None:
    stale = TrackView(1, (0.0, 0.0, 0.3, 0.9), Role.PENDING, 0.9, name="Ada", seen=False)
    current = TrackView(2, stale.box, Role.STILL, 0.8)
    assert display_tracks((stale, current)) == (current,)
