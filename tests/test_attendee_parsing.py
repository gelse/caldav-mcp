"""Round-trip (serialize -> re-parse) tests for attendee and move operations.

These tests verify that ATTENDEE properties written through the icalendar
component API survive a full serialise -> re-parse cycle without a live CalDAV
server. Each fake event holds a real ``icalendar.Event`` component, and its
``save()`` re-parses the serialized payload so we can confirm round-trip
fidelity of the ``mailto:`` address as well as CN / PARTSTAT / RSVP / ROLE.
"""

import unittest
from unittest import mock

from icalendar import Calendar, Event, vCalAddress, vText

import server


class FakeEvent:
    """Stand-in for a caldav Event whose icalendar_component is the VEVENT.

    ``data`` is backed by ``comp.to_ical()`` and is refreshed on ``save()``;
    ``delete()`` records the call for later assertions.
    """

    def __init__(self, ical):
        events = ical.walk("VEVENT")
        self.icalendar_component = events[0]  # _comp reads this attribute
        self.data = self.icalendar_component.to_ical().decode("utf-8")
        self.saves = 0
        self.deleted = False

    def save(self):
        # Re-parse the serialized data so icalendar_component reflects new state.
        parsed = Calendar.from_ical(self.data)
        events = parsed.walk("VEVENT")
        self.icalendar_component = events[0]
        self.saves += 1

    def delete(self):
        self.deleted = True


class FakeCalendar:
    """Stand-in for a caldav Calendar: returns an event by UID, or saves data."""

    def __init__(self, event=None, name=""):
        self._event = event
        self.name = name
        self.saved = []

    def event_by_uid(self, uid):
        return self._event

    def save_event(self, data):
        self.saved.append(data)


class FakePrincipal:
    def __init__(self, calendars):
        self._calendars = calendars

    def calendars(self):
        return self._calendars


class FakeClient:
    def __init__(self):
        pass

    def principal(self):
        return FakePrincipal([])


def patch_attendee_network(fake_cal):
    return [
        mock.patch.object(server, "_resolve_credentials", return_value=("u", "p", "w")),
        mock.patch.object(server, "DAVClient", return_value=FakeClient()),
        mock.patch.object(server, "_get_calendar", return_value=fake_cal),
    ]


def patch_move_network(src_cal, dst_cal):
    return [
        mock.patch.object(server, "_resolve_credentials", return_value=("u", "p", "w")),
        mock.patch.object(server, "DAVClient", return_value=FakeClient()),
        mock.patch.object(
            server,
            "_get_calendar",
            side_effect=lambda client, name: dst_cal if name == dst_cal.name else src_cal,
        ),
    ]


def make_event(uid="roundtrip-uid@caldav-mcp", attendees=()):
    """Build a Calendar with one VEVENT carrying optional ATTENDEE entries."""
    cal = Calendar()
    cal.add("prodid", "-//caldav-mcp//EN")
    cal.add("version", "2.0")
    ev = Event()
    ev.add("uid", uid)
    ev.add("summary", "Roundtrip Meeting")
    ev.add("cn", "Roundtrip Host")
    for email in attendees:
        attendee = vCalAddress("mailto:" + email)
        attendee.params["CN"] = vText(email.split("@")[0])
        attendee.params["PARTSTAT"] = vText("ACCEPTED")
        attendee.params["RSVP"] = vText("TRUE")
        attendee.params["ROLE"] = vText("REQ-PARTICIPANT")
        ev.add("attendee", attendee, encode=False)
    cal.add_component(ev)
    return cal


def attendees_of(event):
    """Return the ATTENDEE list from the event's current component."""
    attendees = event.icalendar_component.get("attendee")
    if attendees is None:
        return []
    if not isinstance(attendees, (list, tuple)):
        return [attendees]
    return list(attendees)


def start_patchers(patchers, owner):
    for p in patchers:
        p.start()
    owner.addCleanup(owner._stop_patchers)


def stop_patchers(patchers):
    for p in patchers:
        p.stop()


class AddAttendeeRoundtripTest(unittest.TestCase):
    def setUp(self):
        self.ical = make_event(attendees=["existing@example.com"])
        self.event = FakeEvent(self.ical)
        self.fake_cal = FakeCalendar(self.event)
        self.patchers = patch_attendee_network(self.fake_cal)
        start_patchers(self.patchers, self)

    def _stop_patchers(self):
        stop_patchers(self.patchers)

    def test_added_attendee_survives_roundtrip(self):
        result = server.caldav_add_attendee(
            uid="roundtrip-uid@caldav-mcp",
            email="new@example.com",
        )
        self.assertTrue(result.startswith("OK:"), msg=result)
        self.event.save()  # simulate server persisting the serialized payload
        attendees = attendees_of(self.event)
        emails = sorted(str(a) for a in attendees)
        self.assertIn("mailto:existing@example.com", emails)
        new = next(a for a in attendees if str(a) == "mailto:new@example.com")
        self.assertEqual(new.params.get("PARTSTAT"), "NEEDS-ACTION")
        self.assertEqual(new.params.get("RSVP"), "TRUE")
        self.assertEqual(new.params.get("ROLE"), "REQ-PARTICIPANT")
        # The serialized data must re-parse into the same attendee set.
        reparsed = Calendar.from_ical(self.event.data)
        reparsed_emails = sorted(
            str(a) for a in reparsed.walk("VEVENT")[0].get("attendee", [])
        )
        self.assertEqual(reparsed_emails, emails)

    def test_added_attendee_serializes_with_mailto_and_params(self):
        self.event.saves = 0
        result = server.caldav_add_attendee(
            uid="roundtrip-uid@caldav-mcp",
            email="carol@example.com",
            role="OPT-PARTICIPANT",
        )
        self.assertTrue(result.startswith("OK:"), msg=result)
        self.assertGreater(self.event.saves, 0)
        payload = self.event.data
        self.assertIn("ATTENDEE", payload)
        self.assertNotIn("Attendee:", payload)  # no raw text interpolation
        reparsed = Calendar.from_ical(payload)
        carol = next(
            a
            for a in reparsed.walk("VEVENT")[0].get("attendee", [])
            if str(a) == "mailto:carol@example.com"
        )
        self.assertEqual(carol.params.get("ROLE"), "OPT-PARTICIPANT")
        self.assertEqual(carol.params.get("PARTSTAT"), "NEEDS-ACTION")
        self.assertEqual(carol.params.get("RSVP"), "TRUE")


class RemoveAttendeeRoundtripTest(unittest.TestCase):
    def setUp(self):
        self.ical = make_event(attendees=["remove@example.com", "keep@example.com"])
        self.event = FakeEvent(self.ical)
        self.fake_cal = FakeCalendar(self.event)
        self.patchers = patch_attendee_network(self.fake_cal)
        start_patchers(self.patchers, self)

    def _stop_patchers(self):
        stop_patchers(self.patchers)

    def test_removed_attendee_does_not_survive_roundtrip(self):
        result = server.caldav_remove_attendee(
            uid="roundtrip-uid@caldav-mcp",
            email="remove@example.com",
        )
        self.assertTrue(result.startswith("OK:"), msg=result)
        self.event.save()
        attendees = attendees_of(self.event)
        emails = [str(a) for a in attendees]
        self.assertNotIn("mailto:remove@example.com", emails)
        self.assertIn("mailto:keep@example.com", emails)
        reparsed = Calendar.from_ical(self.event.data)
        reparsed_attendees = reparsed.walk("VEVENT")[0].get("attendee")
        if reparsed_attendees is None:
            reparsed_emails = []
        elif not isinstance(reparsed_attendees, (list, tuple)):
            reparsed_emails = [str(reparsed_attendees)]
        else:
            reparsed_emails = [str(a) for a in reparsed_attendees]
        self.assertEqual(reparsed_emails, emails)

    def test_remove_not_found_returns_not_found(self):
        result = server.caldav_remove_attendee(
            uid="roundtrip-uid@caldav-mcp",
            email="nobody@example.com",
        )
        self.assertIn("not found", result)
        self.event.save()
        emails = [str(a) for a in attendees_of(self.event)]
        self.assertEqual(
            sorted(emails),
            sorted(["mailto:remove@example.com", "mailto:keep@example.com"]),
        )


class MoveEventRoundtripTest(unittest.TestCase):
    def setUp(self):
        self.ical = make_event(uid="move-uid@caldav-mcp", attendees=["alice@example.com"])
        self.event = FakeEvent(self.ical)
        self.src_cal = FakeCalendar(event=self.event, name="src")
        self.dst_cal = FakeCalendar(name="dst")
        self.patchers = patch_move_network(self.src_cal, self.dst_cal)
        start_patchers(self.patchers, self)

    def _stop_patchers(self):
        stop_patchers(self.patchers)

    def test_move_preserves_attendees_with_new_uid(self):
        result = server.caldav_move_event(
            uid="move-uid@caldav-mcp",
            target_calendar="dst",
        )
        self.assertTrue(result.startswith("OK:"), msg=result)
        self.assertEqual(len(self.dst_cal.saved), 1)
        # Destination payload must re-parse to a VEVENT with a fresh UID that
        # still carries the original attendee.
        reparsed = Calendar.from_ical(self.dst_cal.saved[0])
        ev = reparsed.walk("VEVENT")[0]
        self.assertNotEqual(ev.get("uid"), "move-uid@caldav-mcp")
        self.assertEqual(ev.get("summary"), "Roundtrip Meeting")
        saved_attendees = ev.get("attendee")
        if not isinstance(saved_attendees, (list, tuple)):
            saved_attendees = [saved_attendees]
        self.assertEqual(len(saved_attendees), 1)
        self.assertEqual(str(saved_attendees[0]), "mailto:alice@example.com")
        # Original was deleted.
        self.assertTrue(self.event.deleted)


if __name__ == "__main__":
    unittest.main()
