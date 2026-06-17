# applicant_form_filler.py
"""Generic, label-driven filler for the VFS 'your-details' applicant form.

The form's field set changes per destination country/visa category (the
Angular app renders it via app-dynamic-form/app-dynamic-control), so instead
of hard-coding selectors per field, this reads each visible control's label
text and looks the value up from an applicant config dict. Unmapped labels
are skipped and reported instead of failing, so a country adding/removing a
field doesn't break the whole form fill.

NOTE: the DOM traversal here is reverse-engineered from a static HTML dump
of the VFS site, not verified against a live render. The label/element
relationship (first `<div>` child holding the label text + asterisk span)
held consistently across every field sampled, but it should be the first
thing checked if a field silently fails to map.
"""
import json

LABEL_KEY_MAP = {
    "cover letter id": "cover_letter_id",
    "first name": "first_name",
    "last name": "last_name",
    "gender": "gender",
    "date of birth": "date_of_birth",
    "current nationality": "current_nationality",
    "passport number": "passport_number",
    "passport expiry date": "passport_expiry_date",
    "dial code": "contact_dial_code",
    "contact number": "contact_number",
    "email": "email",
}

JS_COLLECT_FIELDS = """
function getLabel(root) {
    const div = root.querySelector(':scope > div > div');
    if (!div) return '';
    return div.textContent.replace(/\\*/g, '').trim();
}
const results = [];
document.querySelectorAll('app-input-control').forEach(el => {
    if (el.offsetParent === null) return;
    const input = el.querySelector('input[matinput]');
    if (!input) return;
    results.push({label: getLabel(el), id: input.id, kind: 'text'});
});
document.querySelectorAll('app-dropdown').forEach(el => {
    if (el.offsetParent === null) return;
    const select = el.querySelector('mat-select');
    if (!select) return;
    results.push({label: getLabel(el), id: select.id, kind: 'select'});
});
document.querySelectorAll('app-ngb-datepicker').forEach(el => {
    if (el.offsetParent === null) return;
    const input = el.querySelector('input[ngbdatepicker]');
    if (!input) return;
    results.push({label: getLabel(el), id: input.id, kind: 'date'});
});
document.querySelectorAll('.d-block').forEach(el => {
    const header = el.querySelector(':scope > div > div');
    if (!header) return;
    const label = header.textContent.replace(/\\*/g, '').trim();
    if (!/contact number/i.test(label)) return;
    const inputs = el.querySelectorAll('input[matinput]');
    if (inputs.length >= 2) {
        results.push({label: 'Dial Code', id: inputs[0].id, kind: 'text'});
        results.push({label: 'Contact Number', id: inputs[1].id, kind: 'text'});
    }
});
return results;
"""


class ApplicantFormFiller:
    def __init__(self, browser_client):
        self.browser = browser_client

    def fill(self, applicant_config):
        """Fill every visible field on the current 'your-details' form that
        has a matching key in applicant_config. Returns the list of labels
        that had no config mapping (for logging/debugging)."""
        fields = self._collect_fields()
        unmatched = []
        for field in fields:
            key = LABEL_KEY_MAP.get(field["label"].strip().lower())
            if not key or not applicant_config.get(key):
                unmatched.append(field["label"])
                continue
            self._fill_field(field, applicant_config[key])
        if unmatched:
            print(f"Form fields with no config mapping (skipped): {unmatched}")
        return unmatched

    def _collect_fields(self):
        return self.browser.sb.execute_script(JS_COLLECT_FIELDS) or []

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
