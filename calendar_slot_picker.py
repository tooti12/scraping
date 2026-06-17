# calendar_slot_picker.py
"""Reads available appointment dates/times from the book-appointment page
and clicks whichever one the human picked. This is a pure DOM utility - the
"ask a human which one" step lives in booking_flow.py via frontend_bridge,
this module only knows how to read the calendar/time-table and click into
it.

NOTE: like applicant_form_filler.py, the selectors here are reverse
engineered from a static HTML dump, not a live render - the first thing to
check if date/time clicks don't register is whether the click target
(anchor vs. td vs. label) actually carries Angular's click binding.
"""
import json

JS_GET_DATES = """
return Array.from(document.querySelectorAll('td.date-availiable[data-date]')).map(td => {
    const numEl = td.querySelector('.fc-daygrid-day-number');
    return {
        date: td.getAttribute('data-date'),
        label: numEl ? numEl.textContent.trim() : ''
    };
});
"""

JS_GET_TIMES = """
return Array.from(document.querySelectorAll('table.ba-slot-table tbody tr'))
    .filter(tr => !tr.classList.contains('d-none') && tr.querySelector('input.ba-slot-radio'))
    .map(tr => {
        const timeCell = tr.querySelector('td[id^="tv"]');
        const box = tr.querySelector('.ba-slot-box');
        return {
            time: timeCell ? timeCell.textContent.trim() : '',
            row_id: box ? box.id : ''
        };
    })
    .filter(t => t.time && t.row_id);
"""


class CalendarSlotPicker:
    def __init__(self, browser_client):
        self.browser = browser_client

    def select_appointment_type(self):
        """Picks the (only) 'Choose a slot' radio to reveal the calendar."""
        self.browser.sb.uc_click('label:contains("Choose a slot")')

    def get_available_dates(self):
        return self.browser.sb.execute_script(JS_GET_DATES) or []

    def select_date(self, date_str):
        script = f"""
            const target = {json.dumps(date_str)};
            const td = document.querySelector(`td[data-date="${{target}}"]`);
            if (!td) return false;
            const clickTarget = td.querySelector('a.fc-daygrid-day-number') || td;
            clickTarget.click();
            return true;
        """
        if not self.browser.sb.execute_script(script):
            raise ValueError(f"Date {date_str} not found/clickable on calendar")

    def expand_all_times(self, max_clicks=10):
        """The time table only shows a handful of rows by default; the rest
        are behind a repeating 'Load More' button."""
        sb = self.browser.sb
        for _ in range(max_clicks):
            if not sb.is_element_visible('button:contains("Load More")'):
                break
            try:
                sb.uc_click('button:contains("Load More")')
                sb.sleep(0.5)
            except Exception:
                break

    def get_available_times(self):
        self.expand_all_times()
        return self.browser.sb.execute_script(JS_GET_TIMES) or []

    def select_time(self, row_id):
        self.browser.sb.click(f"#{row_id}")
