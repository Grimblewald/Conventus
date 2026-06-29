"""Dynamic form rendering from JSON schema.

Usable via the ``render_form`` function in Python routes or directly
from Jinja templates iterating over schema sections.
"""
from __future__ import annotations

from markupsafe import Markup


COUNTRY_LIST = (
    "Afghanistan", "Albania", "Algeria", "Argentina", "Armenia", "Australia",
    "Austria", "Bangladesh", "Belgium", "Brazil", "Canada", "Chile", "China",
    "Colombia", "Croatia", "Czech Republic", "Denmark", "Egypt", "Estonia",
    "Ethiopia", "Finland", "France", "Germany", "Ghana", "Greece",
    "Hong Kong", "Hungary", "Iceland", "India", "Indonesia", "Iran", "Iraq",
    "Ireland", "Israel", "Italy", "Japan", "Jordan", "Kenya", "Kuwait",
    "Latvia", "Lebanon", "Lithuania", "Malaysia", "Mexico", "Morocco",
    "Netherlands", "New Zealand", "Nigeria", "Norway", "Oman", "Pakistan",
    "Philippines", "Poland", "Portugal", "Qatar", "Romania", "Russia",
    "Saudi Arabia", "Serbia", "Singapore", "Slovakia", "Slovenia",
    "South Africa", "South Korea", "Spain", "Sri Lanka", "Sudan", "Sweden",
    "Switzerland", "Taiwan", "Tanzania", "Thailand", "Tunisia", "Turkey",
    "Uganda", "Ukraine", "United Arab Emirates", "United Kingdom",
    "United States", "Vietnam", "Zimbabwe",
)


def render_form(schema: dict | None, data: dict | None = None,
                errors: dict | None = None) -> Markup:
    """Render a complete form from a JSON schema.

    Returns a Jinja-templated HTML string with all sections and fields.
    """
    if not schema:
        return Markup("")
    if data is None:
        data = {}
    if errors is None:
        errors = {}
    sections = schema.get("sections", [])
    sub_events = schema.get("sub_events", [])
    parts: list[str] = []
    for sec in sections:
        parts.append(_render_section(sec, data, errors))
    for se in sub_events:
        parts.append(_render_sub_event(se, data.get("_sub_events", {}), errors))
    return Markup("\n".join(parts))


def validate_form(schema: dict | None, form_data: dict) -> list[str]:
    """Server-side validation against the schema.

    Returns a list of error messages (empty = valid).
    """
    if not schema:
        return []
    errors: list[str] = []
    for section in schema.get("sections", []):
        for field in section.get("fields", []):
            key = field.get("key", "")
            label = field.get("label", key)
            required = field.get("required", False)
            condition = field.get("condition")
            value = form_data.get(key)

            # Skip validation if field is hidden by condition
            if condition and not _condition_met(condition, form_data):
                continue

            if required and (value is None or (isinstance(value, str) and not value.strip())):
                errors.append(f"{label} is required.")
                continue
            if not required and (value is None or (isinstance(value, str) and not value.strip())):
                continue

            ftype = field.get("type", "text")
            options = field.get("options", [])

            if ftype in ("select", "radio") and options:
                if isinstance(value, str) and value.strip() and value not in options:
                    errors.append(f"Invalid option for {label}.")
            elif ftype == "checkbox-group" and options:
                vals = form_data.getlist(key) if hasattr(form_data, 'getlist') else (value if isinstance(value, list) else [value])
                for v in vals:
                    if v and v.strip() and v not in options:
                        errors.append(f"Invalid option for {label}.")
                        break

    for se in schema.get("sub_events", []):
        sekey = se.get("key", "")
        attending = form_data.get(f"_sub_event_{sekey}_attending")
        if attending == "yes":
            for pf in se.get("preference_fields", []):
                pfkey = pf.get("key", "")
                pflabel = pf.get("label", pfkey)
                pfvalue = form_data.get(f"_sub_event_{sekey}_{pfkey}")
                if pf.get("required") and (pfvalue is None or (isinstance(pfvalue, str) and not pfvalue.strip())):
                    errors.append(f"{pflabel} is required.")

    return errors


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _render_section(sec: dict, data: dict, errors: dict) -> str:
    label = sec.get("label", "")
    key = sec.get("key", "")
    collapsible = sec.get("collapsible", False)
    toggleable = sec.get("toggleable", False)
    fields = sec.get("fields", [])

    sec_id = f"section-{key}" if key else ""
    # Inline styles for conditional visibility
    style = ""
    if toggleable and not data.get(f"_section_toggle_{key}"):
        style = "display:none;"

    header = f"<h3>{label}</h3>" if label else ""
    opener = ""
    closer = ""
    if collapsible:
        header = (f'<details{" open" if not toggleable else ""} id="{sec_id}"'
                  f' style="{style}"><summary>{label}</summary>')
        closer = "</details>"
    elif toggleable:
        header = f'<fieldset id="{sec_id}" style="{style}"><legend>{label}</legend>'
        closer = "</fieldset>"

    field_html = "".join(_render_field(f, data.get(f.get("key")), errors) for f in fields)
    return f"{opener}{header}{field_html}{closer}"


def _render_sub_event(se: dict, se_data: dict, errors: dict) -> str:
    key = se.get("key", "")
    label = se.get("label", key)
    desc = se.get("description", "")
    price = se.get("price", 0)
    currency = se.get("currency", "AUD")
    eligibility = se.get("eligibility_note", "")
    attending = se_data.get(key, {}).get("attending", False)
    attend_name = f"_sub_event_{key}_attending"

    parts = [f'<fieldset class="sub-event-block"><legend>{label}</legend>']
    if desc:
        parts.append(f'<p class="muted">{desc}</p>')
    if eligibility:
        parts.append(f'<p class="muted" style="font-size:13px;"><em>{eligibility}</em></p>')
    if price:
        parts.append(f'<p class="muted">Additional cost: {currency} {price}</p>')
    parts.append(
        f'<div class="field"><label>Attendance</label>'
        f'<label class="check-label"><input type="radio" name="{attend_name}" value="yes"'
        f'{" checked" if attending else ""}> Yes, I would like to attend (+{currency} {price})</label>'
        f'<label class="check-label"><input type="radio" name="{attend_name}" value="no"'
        f'{" checked" if not attending else ""}> No, thank you</label>'
        f'</div>'
    )
    for pf in se.get("preference_fields", []):
        pfkey = pf.get("key", "")
        full_key = f"_sub_event_{key}_{pfkey}"
        parts.append(_render_field({**pf, "key": full_key},
                                   se_data.get(key, {}).get(pfkey), errors))
    parts.append("</fieldset>")
    return "\n".join(parts)


def _render_field(field: dict, value, errors: dict) -> str:
    key = field.get("key", "")
    label = field.get("label", key)
    ftype = field.get("type", "text")
    required = field.get("required", False)
    options = field.get("options", [])
    condition = field.get("condition")
    esc_label = label.replace("&", "&amp;").replace('"', "&quot;")
    esc_key = key.replace("&", "&amp;").replace('"', "&quot;")

    cond_attrs = ""
    cond_style = ""
    if condition:
        cond_field = condition.get("field", "")
        cond_val = condition.get("value")
        cond_contains = condition.get("contains")
        cond_attrs = f' data-cond-field="{cond_field}"'
        if cond_val is not None:
            cond_attrs += f' data-cond-value="{cond_val}"'
        if cond_contains:
            cond_attrs += f' data-cond-contains="{cond_contains}"'
        cond_style = "display:none;"

    error_note = ""
    if esc_key in errors:
        error_note = f'<span class="field-error">{errors[esc_key]}</span>'

    field_wrap_open = (f'<div class="field" data-cond-wrap="{esc_key}"'
                       f' style="{cond_style}"{cond_attrs}>')
    field_wrap_close = "</div>"

    req_mark = ' <span class="req">*</span>' if required else ""

    if ftype == "textarea":
        val = value if value else ""
        return (
            f'{field_wrap_open}'
            f'<label for="{esc_key}">{esc_label}{req_mark}</label>'
            f'<textarea class="textarea" id="{esc_key}" name="{esc_key}">'
            f'{val}</textarea>{error_note}{field_wrap_close}'
        )

    if ftype == "select":
        opts = "".join(
            f'<option value="{o}"{" selected" if value == o else ""}>{o}</option>'
            for o in options
        )
        return (
            f'{field_wrap_open}'
            f'<label for="{esc_key}">{esc_label}{req_mark}</label>'
            f'<select class="select" id="{esc_key}" name="{esc_key}">'
            f'<option value="">—</option>{opts}</select>'
            f'{error_note}{field_wrap_close}'
        )

    if ftype == "country":
        opts = "".join(
            f'<option value="{c}"{" selected" if value == c else ""}>{c}</option>'
            for c in COUNTRY_LIST
        )
        return (
            f'{field_wrap_open}'
            f'<label for="{esc_key}">{esc_label}{req_mark}</label>'
            f'<select class="select" id="{esc_key}" name="{esc_key}">'
            f'<option value="">—</option>{opts}</select>'
            f'{error_note}{field_wrap_close}'
        )

    if ftype == "radio":
        radios = "".join(
            f'<label class="check-label"><input type="radio" name="{esc_key}"'
            f' value="{o}"{" checked" if value == o else ""}> {o}</label>'
            for o in options
        )
        return (
            f'{field_wrap_open}'
            f'<span class="label">{esc_label}{req_mark}</span>'
            f'{radios}{error_note}{field_wrap_close}'
        )

    if ftype == "checkbox":
        checked = " checked" if value else ""
        return (
            f'{field_wrap_open}'
            f'<label class="check-label"><input type="checkbox" name="{esc_key}"'
            f' value="1"{checked}> {esc_label}{req_mark}</label>'
            f'{error_note}{field_wrap_close}'
        )

    if ftype == "checkbox-group":
        vals = value if isinstance(value, list) else [value]
        checkboxes = "".join(
            f'<label class="check-label"><input type="checkbox"'
            f' name="{esc_key}" value="{o}"'
            f'{" checked" if o in vals else ""}> {o}</label>'
            for o in options
        )
        return (
            f'{field_wrap_open}'
            f'<span class="label">{esc_label}{req_mark}</span>'
            f'{checkboxes}{error_note}{field_wrap_close}'
        )

    if ftype == "number":
        return (
            f'{field_wrap_open}'
            f'<label for="{esc_key}">{esc_label}{req_mark}</label>'
            f'<input class="input" id="{esc_key}" name="{esc_key}" type="number"'
            f' value="{value or ""}">{error_note}{field_wrap_close}'
        )

    if ftype == "tel":
        return (
            f'{field_wrap_open}'
            f'<label for="{esc_key}">{esc_label}{req_mark}</label>'
            f'<input class="input" id="{esc_key}" name="{esc_key}" type="tel"'
            f' value="{value or ""}">{error_note}{field_wrap_close}'
        )

    if ftype == "email":
        return (
            f'{field_wrap_open}'
            f'<label for="{esc_key}">{esc_label}{req_mark}</label>'
            f'<input class="input" id="{esc_key}" name="{esc_key}" type="email"'
            f' value="{value or ""}">{error_note}{field_wrap_close}'
        )

    # Default: text
    return (
        f'{field_wrap_open}'
        f'<label for="{esc_key}">{esc_label}{req_mark}</label>'
        f'<input class="input" id="{esc_key}" name="{esc_key}" type="text"'
        f' value="{value or ""}">{error_note}{field_wrap_close}'
    )


def _condition_met(condition: dict, form_data: dict) -> bool:
    field = condition.get("field", "")
    value = condition.get("value")
    contains = condition.get("contains")
    current = form_data.get(field)

    if value is not None:
        if isinstance(value, bool):
            return bool(current)
        return str(current) == str(value)
    if contains:
        if isinstance(current, list):
            return contains in current
        return contains in str(current or "")
    return False
