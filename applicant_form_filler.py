# applicant_form_filler.py
"""Generic, label-driven filler for the VFS 'your-details' applicant form.

The form's field set changes per destination country/visa category (the
Angular app renders it via app-dynamic-form/app-dynamic-control), so instead
of hard-coding selectors per field, this discovers each visible control's
label (and whether VFS marks it required with a '*') straight from the live
page, and fills it from a values dict keyed by that exact label text. The
caller (booking_flow.py) fetches the fields, asks the dashboard for values,
then calls fill_dynamic() with whatever the user submitted.

NOTE: the DOM traversal here is reverse-engineered from a static HTML dump
of the VFS site, not verified against a live render. The label/element
relationship (first `<div>` child holding the label text + asterisk span)
held consistently across every field sampled, but it should be the first
thing checked if a field silently fails to map.
"""
import json

JS_COLLECT_FIELDS = """
function getLabelInfo(root) {
    const div = root.querySelector(':scope > div > div');
    if (!div) return { label: '', required: false };
    const raw = div.textContent.trim();
    return { label: raw.replace(/\\*/g, '').trim(), required: raw.indexOf('*') !== -1 };
}
const results = [];
document.querySelectorAll('app-input-control').forEach(el => {
    if (el.offsetParent === null) return;
    const input = el.querySelector('input[matinput]');
    if (!input) return;
    const info = getLabelInfo(el);
    results.push({label: info.label, required: info.required, id: input.id, kind: 'text'});
});
document.querySelectorAll('app-dropdown').forEach(el => {
    if (el.offsetParent === null) return;
    const select = el.querySelector('mat-select');
    if (!select) return;
    const info = getLabelInfo(el);
    results.push({label: info.label, required: info.required, id: select.id, kind: 'select'});
});
document.querySelectorAll('app-ngb-datepicker').forEach(el => {
    if (el.offsetParent === null) return;
    const input = el.querySelector('input[ngbdatepicker]');
    if (!input) return;
    const info = getLabelInfo(el);
    results.push({label: info.label, required: info.required, id: input.id, kind: 'date'});
});
document.querySelectorAll('.d-block').forEach(el => {
    const header = el.querySelector(':scope > div > div');
    if (!header) return;
    const rawHeader = header.textContent.trim();
    const label = rawHeader.replace(/\\*/g, '').trim();
    if (!/contact number/i.test(label)) return;
    const required = rawHeader.indexOf('*') !== -1;
    const inputs = el.querySelectorAll('input[matinput]');
    if (inputs.length >= 2) {
        results.push({label: 'Dial Code', required: required, id: inputs[0].id, kind: 'text'});
        results.push({label: 'Contact Number', required: required, id: inputs[1].id, kind: 'text'});
    }
});
return results;
"""


class ApplicantFormFiller:
    def __init__(self, browser_client):
        self.browser = browser_client

    def collect_fields(self):
        """Read every visible field on the current 'your-details' form
        straight from the DOM. Returns
        [{"label": ..., "required": bool, "id": ..., "kind": "text"|"select"|"date"}, ...]
        """
        return self.browser.sb.execute_script(JS_COLLECT_FIELDS) or []

    def fill_dynamic(self, fields, values):
        """Fill every field using values keyed by its exact label text (as
        returned by collect_fields()). Returns the list of labels left
        blank (for logging/debugging)."""
        unmatched = []
        for field in fields:
            value = values.get(field["label"])
            if not value:
                unmatched.append(field["label"])
                continue
            self._fill_field(field, value)
        if unmatched:
            print(f"Form fields left blank (no value submitted): {unmatched}")
        return unmatched

    def _fill_field(self, field, value):
        sb = self.browser.sb
        if field["kind"] in ("text", "date"):
            sb.cdp.press_keys(f"#{field['id']}", str(value))
        elif field["kind"] == "select":
            self._select_dropdown_option(field["id"], str(value))

    def _select_dropdown_option(self, select_id, option_text):
        sb = self.browser.sb
        sb.click(f"#{select_id}", scroll=True)
        sb.sleep(0.5)
        script = f"""
            const target = {json.dumps(option_text.lower())};
            const opts = Array.from(document.querySelectorAll('mat-option'));
            let match = opts.find(o => o.textContent.trim().toLowerCase() === target);
            if (!match) match = opts.find(o => o.textContent.trim().toLowerCase().includes(target));
            return match ? match.id : null;
        """
        option_id = sb.execute_script(script)
        if not option_id:
            raise ValueError(f"Dropdown option '{option_text}' not found for #{select_id}")
        sb.click(f"#{option_id}")

    def save(self):
        self.browser.sb.uc_click('button:contains("Save")')
