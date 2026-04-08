import os
import re
import tkinter as tk
from tkinter import ttk, messagebox
import json
import sqlite3
import threading
import traceback
from datetime import datetime
from html.parser import HTMLParser
from urllib.request import urlopen
from urllib.parse import urljoin
from openai import OpenAI
from PIL import Image, ImageTk

# ---------------- AI CLIENT ----------------
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
USE_DEMO_MODE = not OPENROUTER_API_KEY

client = None
if not USE_DEMO_MODE:
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY
    )

CURRENT_BUSINESS_NAME = ""
CURRENT_WEBSITE_SOURCE = ""
CURRENT_EXTERNAL_SOURCE = ""
CURRENT_EMAIL_SUBJECT = ""


class SimpleHTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        text = data.strip()
        if text:
            self.parts.append(text)

    def get_text(self):
        return "\n".join(self.parts)


def read_source(source_value):
    source_value = source_value.strip()

    if source_value.startswith("http://") or source_value.startswith("https://"):
        with urlopen(source_value) as response:
            return response.read().decode("utf-8", errors="ignore")

    with open(source_value, "r", encoding="utf-8") as f:
        return f.read()


def extract_visible_text(html_content):
    parser = SimpleHTMLTextExtractor()
    parser.feed(html_content)
    return parser.get_text()


def extract_internal_links(html_content, current_source):
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html_content, flags=re.IGNORECASE)
    links = []

    for href in hrefs:
        href = href.strip()
        if not href:
            continue

        if href.startswith("#"):
            continue

        if href.lower().startswith(("mailto:", "tel:", "javascript:")):
            continue

        if not href.lower().endswith(".html"):
            continue

        if current_source.startswith("http://") or current_source.startswith("https://"):
            full_link = urljoin(current_source, href)
            links.append(full_link)
        else:
            full_link = os.path.normpath(os.path.join(os.path.dirname(current_source), href))
            links.append(full_link)

    return links


def crawl_site_from_main(main_source, max_pages=5):
    visited = set()
    to_visit = [main_source]
    collected_sources = []
    all_text_parts = []

    while to_visit and len(visited) < max_pages:
        current = to_visit.pop(0)

        normalized_key = current.lower()
        if normalized_key in visited:
            continue

        try:
            html = read_source(current)
        except Exception:
            continue

        visited.add(normalized_key)
        collected_sources.append(current)

        text = extract_visible_text(html)
        all_text_parts.append(f"\n--- PAGE: {current} ---\n{text}")

        internal_links = extract_internal_links(html, current)
        for link in internal_links:
            link_key = link.lower()
            if link_key not in visited and link not in to_visit:
                to_visit.append(link)

    return "\n".join(all_text_parts), collected_sources


def safe_json_loads(content):
    content = content.strip()

    if content.startswith("```"):
        content = content.replace("```json", "").replace("```", "").strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise ValueError(f"Model did not return valid JSON.\n\nRaw response:\n{content}")


def clean_spaces(text):
    return re.sub(r"\s+", " ", text).strip()


def unique_keep_order(items):
    result = []
    seen = set()

    for item in items:
        normalized = item.strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(item.strip())

    return result


def find_first_match(patterns, text, flags=re.IGNORECASE):
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            return clean_spaces(match.group(1))
    return ""


def extract_phone_candidates_from_html(html_text):
    candidates = []

    tel_matches = re.findall(r'tel:([+\d][\d\-\s]+)', html_text, flags=re.IGNORECASE)
    candidates.extend(tel_matches)

    whatsapp_matches = re.findall(
        r'(?:wa\.me/|api\.whatsapp\.com/send\?phone=)(\d{9,15})',
        html_text,
        flags=re.IGNORECASE
    )
    candidates.extend(whatsapp_matches)

    generic_matches = re.findall(
        r'(\+972[\-\s]?\d[\-\s]?\d{3}[\-\s]?\d{4}|\b0\d{1,2}[\-\s]?\d{3}[\-\s]?\d{4}\b|\b05\d[\-\s]?\d{3}[\-\s]?\d{4}\b|\b05\d{8}\b|\b0\d{8,9}\b)',
        html_text
    )
    candidates.extend(generic_matches)

    cleaned = []
    seen = set()

    for candidate in candidates:
        normalized = clean_spaces(candidate)
        digits_only = re.sub(r"\D", "", normalized)

        if len(digits_only) >= 9 and digits_only not in seen:
            seen.add(digits_only)
            cleaned.append(normalized)

    return cleaned


def normalize_israeli_phone(phone):
    phone = clean_spaces(phone)
    digits = re.sub(r"\D", "", phone)

    if digits.startswith("972") and len(digits) >= 11:
        local = "0" + digits[3:]
        return local[:10]

    if len(digits) >= 10:
        return digits[:10]

    if len(digits) == 9:
        return digits

    return phone


def find_phone(text, raw_html=""):
    candidates = []

    text_patterns = [
        r'(\+972[\-\s]?\d[\-\s]?\d{3}[\-\s]?\d{4})',
        r'(\b0\d{1,2}[\-\s]?\d{3}[\-\s]?\d{4}\b)',
        r'(\b05\d[\-\s]?\d{3}[\-\s]?\d{4}\b)',
        r'(\b05\d{8}\b)',
        r'(\b0\d{8,9}\b)'
    ]

    for pattern in text_patterns:
        matches = re.findall(pattern, text)
        candidates.extend(matches)

    if raw_html:
        candidates.extend(extract_phone_candidates_from_html(raw_html))

    seen = set()
    for candidate in candidates:
        normalized = normalize_israeli_phone(candidate)
        digits_only = re.sub(r"\D", "", normalized)

        if digits_only in seen:
            continue
        seen.add(digits_only)

        if len(digits_only) in (9, 10):
            return normalized

    return ""


def find_email(text, raw_html=""):
    combined = text + "\n" + raw_html

    match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', combined)
    if match:
        return match.group(0).strip()

    mailto_match = re.search(r'mailto:([\w\.-]+@[\w\.-]+\.\w+)', raw_html, re.IGNORECASE)
    if mailto_match:
        return mailto_match.group(1).strip()

    return ""


def find_business_name(text):
    patterns = [
        r'(?:business name|company name|שם העסק|שם החברה)\s*[:\-]?\s*([^\n\r]{2,80})',
        r'^\s*([A-Z][A-Za-z0-9 &\-\']{2,60})\s*$'
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            candidate = clean_spaces(match.group(1))
            if len(candidate) <= 60:
                return candidate

    lines = [clean_spaces(line) for line in text.splitlines()]
    for line in lines:
        if 3 <= len(line) <= 50 and not re.search(r'@|http|www|phone|טלפון|כתובת', line, re.IGNORECASE):
            return line

    return "Client Business"


def find_service_area(text):
    areas = [
        ("קריות", "Krayot area"),
        ("krayot", "Krayot area"),
        ("חיפה", "Haifa area"),
        ("haifa", "Haifa area"),
        ("צפון", "Northern Israel"),
        ("north", "Northern Israel"),
        ("תל אביב", "Tel Aviv area"),
        ("tel aviv", "Tel Aviv area"),
        ("ירושלים", "Jerusalem area"),
        ("jerusalem", "Jerusalem area")
    ]

    found = []
    lower_text = text.lower()

    for needle, label in areas:
        if needle.lower() in lower_text and label not in found:
            found.append(label)

    if found:
        return ", ".join(found)

    patterns = [
        r'(?:service area|areas served|we serve|אזור שירות|אזורי שירות)\s*[:\-]?\s*([^\n\r]{2,100})'
    ]

    found_pattern = find_first_match(patterns, text)
    return found_pattern if found_pattern else ""


def find_opening_hours(text):
    patterns = [
        r'(?:opening hours|hours|working hours|שעות פעילות|שעות עבודה)\s*[:\-]?\s*([^\n\r]{3,100})',
        r'((?:sun|mon|tue|wed|thu|fri|sat)[^\n\r]{5,100})',
        r'((?:א[׳\'"]?|ב|ג|ד|ה|ו|ש)[^\n\r]{5,100})'
    ]

    return find_first_match(patterns, text)


def find_address(text):
    patterns = [
        r'(?:address|כתובת)\s*[:\-]?\s*([^\n\r]{4,120})'
    ]
    return find_first_match(patterns, text)


def find_rating(text):
    patterns = [
        r'(?:rating|דירוג)\s*[:\-]?\s*([0-5](?:\.\d)?)',
        r'([0-5](?:\.\d)?)\s*(?:/ ?5|stars?)'
    ]
    return find_first_match(patterns, text)


def detect_business_type(text):
    lower_text = text.lower()

    rules = [
        (
            ["מזג", "air conditioner", "air conditioning", "ac repair", "hvac"],
            "Air Conditioner Technician"
        ),
        (
            ["electric", "electrician", "חשמל", "electrical"],
            "Electrician"
        ),
        (
            ["plumb", "plumber", "אינסטלטור", "pipes"],
            "Plumber"
        ),
        (
            ["cleaning", "ניקיון", "clean"],
            "Cleaning Services"
        ),
        (
            ["lawyer", "attorney", "עו\"ד", "עורך דין"],
            "Legal Services"
        ),
        (
            ["dentist", "dental", "רופא שיניים"],
            "Dental Clinic"
        )
    ]

    for keywords, label in rules:
        if any(keyword in lower_text for keyword in keywords):
            return label

    return "Local Service Business"


def detect_services_and_categories(text):
    lower_text = text.lower()

    service_rules = [
        (["air conditioner installation", "installation", "התקנת מזגנים", "התקנה"], "Air conditioner installation", "Air conditioning"),
        (["air conditioner repair", "repair", "תיקון מזגנים", "תיקון"], "Air conditioner repair", "Repairs"),
        (["maintenance", "preventive maintenance", "תחזוקה"], "Preventive maintenance", "Maintenance"),
        (["emergency", "24/7", "urgent", "חירום", "דחוף"], "Emergency service", "Emergency services"),
        (["cleaning", "ניקוי"], "Cleaning service", "Cleaning"),
        (["consultation", "ייעוץ"], "Consultation", "Professional services"),
        (["inspection", "בדיקה"], "Inspection", "Professional services"),
        (["sales", "מכירה"], "Sales", "Sales")
    ]

    services = []
    categories = []

    for keywords, service_name, category_name in service_rules:
        if any(keyword in lower_text for keyword in keywords):
            services.append(service_name)
            categories.append(category_name)

    business_type = detect_business_type(text)
    if business_type == "Air Conditioner Technician":
        categories.extend(["Air conditioning", "Home services"])
    elif business_type == "Electrician":
        categories.extend(["Electrical services", "Home services"])
    elif business_type == "Plumber":
        categories.extend(["Plumbing", "Home services"])

    services = unique_keep_order(services)
    categories = unique_keep_order(categories)

    if not services:
        services = ["Main service from website"]
    if not categories:
        categories = ["Local services"]

    return services, categories


def detect_target_customers(text):
    lower_text = text.lower()
    results = []

    if any(word in lower_text for word in ["home", "house", "apartment", "דירה", "בית", "residential"]):
        results.append("Homeowners")

    if any(word in lower_text for word in ["office", "business", "commercial", "משרד", "עסק"]):
        results.append("Small businesses")

    if any(word in lower_text for word in ["shop", "store", "retail", "חנות"]):
        results.append("Retail businesses")

    if not results:
        results = ["Local customers"]

    return unique_keep_order(results)


def detect_usp(text):
    lower_text = text.lower()
    results = []

    if any(word in lower_text for word in ["fast", "quick", "מהיר"]):
        results.append("Fast service")

    if any(word in lower_text for word in ["24/7", "emergency", "חירום", "urgent"]):
        results.append("Emergency availability")

    if any(word in lower_text for word in ["krayot", "קריות", "local", "local service", "אזורי"]):
        results.append("Strong local coverage")

    if any(word in lower_text for word in ["experienced", "professional", "מנוסה", "מקצועי"]):
        results.append("Professional service")

    if not results:
        results = ["Local service coverage"]

    return unique_keep_order(results)


def build_description(business_type, service_area, services):
    first_service = services[0] if services else "local services"

    if business_type and service_area:
        return f"{business_type} providing {first_service.lower()} in the {service_area}."
    if business_type:
        return f"{business_type} providing local professional services."
    return "Local business services identified from the provided digital assets."


def build_needs_verification(data):
    needs = []

    if not data["phone"]:
        needs.append("Preferred contact phone number")
    if not data["email"]:
        needs.append("Business email address")
    if not data["address"]:
        needs.append("Business address")
    if not data["opening_hours"]:
        needs.append("Opening hours")
    if not data["service_area"]:
        needs.append("Exact service area")
    if not data["services"] or data["services"] == ["Main service from website"]:
        needs.append("Exact service list")
    if not data["categories"] or data["categories"] == ["Local services"]:
        needs.append("Business categories")
    needs.append("Business media/assets")
    needs.append("Priority services to highlight")

    return unique_keep_order(needs)


def build_missing_fields(data):
    missing = []

    if not data["phone"]:
        missing.append("Phone")
    if not data["email"]:
        missing.append("Email")
    if not data["address"]:
        missing.append("Address")
    if not data["opening_hours"]:
        missing.append("Opening hours")
    if not data["service_area"]:
        missing.append("Service area")
    if not data["rating"]:
        missing.append("Rating")

    missing.append("Business logo")
    missing.append("Gallery images")

    return unique_keep_order(missing)


def get_demo_extraction_result(website_text, external_text, website_html="", external_html=""):
    combined_text = f"{website_text}\n{external_text}"
    combined_html = f"{website_html}\n{external_html}"

    business_name = find_business_name(combined_text)
    phone = find_phone(combined_text, combined_html)
    email = find_email(combined_text, combined_html)
    address = find_address(combined_text)
    service_area = find_service_area(combined_text)
    opening_hours = find_opening_hours(combined_text)
    rating = find_rating(combined_text)
    business_type = detect_business_type(combined_text)
    services, categories = detect_services_and_categories(combined_text)
    target_customers = detect_target_customers(combined_text)
    usp = detect_usp(combined_text)
    description = build_description(business_type, service_area, services)

    data = {
        "business_name": business_name or "Client Business",
        "phone": phone,
        "email": email,
        "address": address,
        "service_area": service_area,
        "opening_hours": opening_hours,
        "rating": rating,
        "business_type": business_type,
        "description": description,
        "services": services,
        "categories": categories,
        "target_customers": target_customers,
        "usp": usp,
        "needs_verification": [],
        "missing_fields": [],
        "source_summary": {
            "website_used": True,
            "external_listing_used": True
        }
    }

    data["needs_verification"] = build_needs_verification(data)
    data["missing_fields"] = build_missing_fields(data)

    return data


def normalize_extracted_data(data):
    expected_defaults = {
        "business_name": "",
        "phone": "",
        "email": "",
        "address": "",
        "service_area": "",
        "opening_hours": "",
        "rating": "",
        "business_type": "",
        "description": "",
        "services": [],
        "categories": [],
        "target_customers": [],
        "usp": [],
        "needs_verification": [],
        "missing_fields": [],
        "source_summary": {
            "website_used": True,
            "external_listing_used": True
        }
    }

    for key, default_value in expected_defaults.items():
        if key not in data:
            data[key] = default_value

    list_fields = [
        "services",
        "categories",
        "target_customers",
        "usp",
        "needs_verification",
        "missing_fields"
    ]

    for field in list_fields:
        if not isinstance(data[field], list):
            data[field] = []

    if not isinstance(data["source_summary"], dict):
        data["source_summary"] = {
            "website_used": True,
            "external_listing_used": True
        }

    return data


def extract_with_ai(website_text, external_text, website_html="", external_html=""):
    if USE_DEMO_MODE:
        return normalize_extracted_data(
            get_demo_extraction_result(website_text, external_text, website_html, external_html)
        )

    prompt = f"""
You are an AI onboarding assistant for Zap.

You are given:
1. Text extracted from a client's business website
2. Text extracted from an external business directory listing that represents the client's Dapei Zahav mini-site

Your job is to merge the information into one structured client onboarding record.

Return ONLY valid JSON.
Do not add explanations.
Do not wrap the JSON in markdown.

Return this exact structure:

{{
  "business_name": "",
  "phone": "",
  "email": "",
  "address": "",
  "service_area": "",
  "opening_hours": "",
  "rating": "",
  "business_type": "",
  "description": "",
  "services": [],
  "categories": [],
  "target_customers": [],
  "usp": [],
  "needs_verification": [],
  "missing_fields": [],
  "source_summary": {{
    "website_used": true,
    "external_listing_used": true
  }}
}}

Rules:
- Merge both sources carefully.
- Prefer the most specific and complete value when there is overlap.
- Normalize duplicate services and categories.
- Keep services and categories short and clean.
- "description" should be one short sentence.
- "usp" means selling points such as fast service, emergency service, local coverage, home and small business support.
- "needs_verification" should include details that should be confirmed during onboarding.
- "missing_fields" should include important missing details not found in the sources.
- If a string field is missing, return "".
- If a list field is missing, return [].
- Return JSON only.

BUSINESS WEBSITE TEXT:
{website_text[:12000]}

EXTERNAL DIRECTORY TEXT:
{external_text[:6000]}
"""

    try:
        response = client.chat.completions.create(
            model="openrouter/free",
            messages=[{"role": "user", "content": prompt}]
        )
    except Exception as e:
        raise RuntimeError(f"AI request failed:\n{e}")

    try:
        content = response.choices[0].message.content.strip()
    except Exception:
        raise RuntimeError(f"Unexpected AI response:\n{response}")

    data = safe_json_loads(content)
    return normalize_extracted_data(data)


def generate_onboarding_script(data):
    if data["services"]:
        services_text = "\n".join(f"- {service}" for service in data["services"])
    else:
        services_text = "- Services not identified"

    if data["needs_verification"]:
        verification_text = "\n".join(f"- {item}" for item in data["needs_verification"])
    else:
        verification_text = "- Preferred contact details\n- Exact services\n- Opening hours\n- Business media/assets"

    return f"""Hello, thank you for joining Zap.

I reviewed the available details for {data['business_name']}.
I found that your business operates in {data['service_area'] or 'the relevant service area'}.

The services currently identified are:
{services_text}

During onboarding, I would like to confirm:
{verification_text}

If anything is missing or incorrect, we will update it together."""


def generate_onboarding_email(data):
    subject = f"Welcome to Zap - onboarding for {data['business_name']}"

    body = f"""Hello {data['business_name']},

Thank you for joining Zap.

We reviewed the information currently available about your business and prepared your onboarding record.

Here is what we identified:
- Phone: {data['phone'] or 'Not found yet'}
- Email: {data['email'] or 'Not found yet'}
- Address: {data['address'] or 'Not found yet'}
- Service Area: {data['service_area'] or 'Not found yet'}
- Opening Hours: {data['opening_hours'] or 'Not found yet'}

Main services:
"""

    if data["services"]:
        for service in data["services"]:
            body += f"- {service}\n"
    else:
        body += "- No services identified yet\n"

    body += """

As part of onboarding, we would like to confirm your service details, business assets, opening hours, and any urgent or promotional services you would like to highlight.

If you need any assistance, our onboarding team will be happy to help.

Best regards,
Zap Onboarding Team
"""
    return subject, body


def save_email_draft(subject, body):
    with open("email_draft.txt", "w", encoding="utf-8") as f:
        f.write("Subject: " + subject + "\n\n")
        f.write(body)


def save_crm_payload(data, onboarding_script, email_subject, email_body, website_sources, external_source):
    payload = {
        "business_name": data["business_name"],
        "client_card": data,
        "onboarding_script": onboarding_script,
        "email_draft": {
            "subject": email_subject,
            "body": email_body
        },
        "crm_status": "ready_for_crm",
        "processing_mode": "demo" if USE_DEMO_MODE else "live_ai",
        "website_sources": website_sources,
        "external_source": external_source,
        "saved_at": datetime.now().isoformat(timespec="seconds")
    }

    with open("crm_payload.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def init_db():
    conn = sqlite3.connect("crm_demo.db")
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_name TEXT,
        phone TEXT,
        email TEXT,
        address TEXT,
        service_area TEXT,
        opening_hours TEXT,
        rating TEXT,
        business_type TEXT,
        description TEXT,
        services_json TEXT,
        categories_json TEXT,
        target_customers_json TEXT,
        usp_json TEXT,
        needs_verification_json TEXT,
        missing_fields_json TEXT,
        onboarding_script TEXT,
        onboarding_email_subject TEXT,
        onboarding_email_body TEXT,
        email_final_subject TEXT,
        email_final_body TEXT,
        email_status TEXT,
        email_sent_at TEXT,
        website_source TEXT,
        external_source TEXT,
        last_updated TEXT
    )
    """)

    conn.commit()
    conn.close()


def ensure_db_columns():
    conn = sqlite3.connect("crm_demo.db")
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(clients)")
    existing_columns = {row[1] for row in cur.fetchall()}

    columns_to_add = {
        "email_final_subject": "TEXT",
        "email_final_body": "TEXT",
        "email_status": "TEXT",
        "email_sent_at": "TEXT"
    }

    for column_name, column_type in columns_to_add.items():
        if column_name not in existing_columns:
            cur.execute(f"ALTER TABLE clients ADD COLUMN {column_name} {column_type}")

    conn.commit()
    conn.close()


def save_to_crm(data, onboarding_script, email_subject, email_body, website_source, external_source):
    conn = sqlite3.connect("crm_demo.db")
    cur = conn.cursor()

    now = datetime.now().isoformat(timespec="seconds")

    cur.execute("SELECT id FROM clients WHERE business_name = ?", (data["business_name"],))
    existing = cur.fetchone()

    if existing:
        cur.execute("""
        UPDATE clients
        SET phone = ?, email = ?, address = ?, service_area = ?, opening_hours = ?,
            rating = ?, business_type = ?, description = ?, services_json = ?, categories_json = ?,
            target_customers_json = ?, usp_json = ?, needs_verification_json = ?, missing_fields_json = ?,
            onboarding_script = ?, onboarding_email_subject = ?, onboarding_email_body = ?,
            website_source = ?, external_source = ?, last_updated = ?,
            email_status = COALESCE(email_status, 'draft')
        WHERE business_name = ?
        """, (
            data["phone"],
            data["email"],
            data["address"],
            data["service_area"],
            data["opening_hours"],
            data["rating"],
            data["business_type"],
            data["description"],
            json.dumps(data["services"], ensure_ascii=False),
            json.dumps(data["categories"], ensure_ascii=False),
            json.dumps(data["target_customers"], ensure_ascii=False),
            json.dumps(data["usp"], ensure_ascii=False),
            json.dumps(data["needs_verification"], ensure_ascii=False),
            json.dumps(data["missing_fields"], ensure_ascii=False),
            onboarding_script,
            email_subject,
            email_body,
            website_source,
            external_source,
            now,
            data["business_name"]
        ))
    else:
        cur.execute("""
        INSERT INTO clients (
            business_name, phone, email, address, service_area, opening_hours,
            rating, business_type, description, services_json, categories_json,
            target_customers_json, usp_json, needs_verification_json, missing_fields_json,
            onboarding_script, onboarding_email_subject, onboarding_email_body,
            email_final_subject, email_final_body, email_status, email_sent_at,
            website_source, external_source, last_updated
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["business_name"],
            data["phone"],
            data["email"],
            data["address"],
            data["service_area"],
            data["opening_hours"],
            data["rating"],
            data["business_type"],
            data["description"],
            json.dumps(data["services"], ensure_ascii=False),
            json.dumps(data["categories"], ensure_ascii=False),
            json.dumps(data["target_customers"], ensure_ascii=False),
            json.dumps(data["usp"], ensure_ascii=False),
            json.dumps(data["needs_verification"], ensure_ascii=False),
            json.dumps(data["missing_fields"], ensure_ascii=False),
            onboarding_script,
            email_subject,
            email_body,
            email_subject,
            email_body,
            "draft",
            "",
            website_source,
            external_source,
            now
        ))

    conn.commit()
    conn.close()


def update_email_status_in_crm(business_name, final_subject, final_body, status):
    conn = sqlite3.connect("crm_demo.db")
    cur = conn.cursor()

    sent_at = datetime.now().isoformat(timespec="seconds") if status == "sent" else ""

    cur.execute("""
    UPDATE clients
    SET email_final_subject = ?,
        email_final_body = ?,
        email_status = ?,
        email_sent_at = ?,
        last_updated = ?
    WHERE business_name = ?
    """, (
        final_subject,
        final_body,
        status,
        sent_at,
        datetime.now().isoformat(timespec="seconds"),
        business_name
    ))

    conn.commit()
    conn.close()


def list_to_text(items, fallback="None"):
    if items:
        return "\n".join(f"- {item}" for item in items)
    return fallback


def fill_text_widget(widget, text):
    widget.config(state="normal")
    widget.delete("1.0", tk.END)
    widget.insert(tk.END, text)
    widget.config(state="disabled")


def set_text_widget(widget, text, editable=False):
    widget.config(state="normal")
    widget.delete("1.0", tk.END)
    widget.insert(tk.END, text)
    widget.config(state="normal" if editable else "disabled")


def get_email_box_text():
    return email_box.get("1.0", tk.END).strip()


def parse_email_box(text):
    lines = text.splitlines()

    if lines and lines[0].startswith("Subject:"):
        subject = lines[0].replace("Subject:", "", 1).strip()
        body = "\n".join(lines[2:]).strip() if len(lines) > 2 else ""
    else:
        subject = CURRENT_EMAIL_SUBJECT
        body = text.strip()

    return subject, body


def enable_email_edit():
    if not CURRENT_BUSINESS_NAME:
        messagebox.showwarning("No client loaded", "Please create a client card first.")
        return

    email_box.config(state="normal")
    change_button.config(state="disabled")
    save_changes_button.config(state="normal")
    send_button.config(state="disabled")


def save_email_changes():
    global CURRENT_EMAIL_SUBJECT

    if not CURRENT_BUSINESS_NAME:
        messagebox.showwarning("No client loaded", "Please create a client card first.")
        return

    final_text = get_email_box_text()
    final_subject, final_body = parse_email_box(final_text)

    CURRENT_EMAIL_SUBJECT = final_subject
    update_email_status_in_crm(CURRENT_BUSINESS_NAME, final_subject, final_body, "edited")
    save_email_draft(final_subject, final_body)

    email_box.config(state="disabled")
    change_button.config(state="normal")
    save_changes_button.config(state="disabled")
    send_button.config(state="normal")

    messagebox.showinfo("Saved", "Email draft changes were saved to the CRM simulation.")


def send_email_simulation():
    if not CURRENT_BUSINESS_NAME:
        messagebox.showwarning("No client loaded", "Please create a client card first.")
        return

    final_text = get_email_box_text()
    final_subject, final_body = parse_email_box(final_text)

    update_email_status_in_crm(CURRENT_BUSINESS_NAME, final_subject, final_body, "sent")
    save_email_draft(final_subject, final_body)

    messagebox.showinfo(
        "Email sent (simulation)",
        "The email was marked as sent and saved in the CRM simulation database."
    )


def place_welcome_card():
    welcome_card.place(relx=0.5, rely=0.47, anchor="center", relwidth=0.42, relheight=0.40)


def show_result_screen(data, onboarding_script, email_subject, email_body, website_source, external_source):
    global CURRENT_BUSINESS_NAME, CURRENT_WEBSITE_SOURCE, CURRENT_EXTERNAL_SOURCE, CURRENT_EMAIL_SUBJECT

    CURRENT_BUSINESS_NAME = data["business_name"]
    CURRENT_WEBSITE_SOURCE = website_source
    CURRENT_EXTERNAL_SOURCE = external_source
    CURRENT_EMAIL_SUBJECT = email_subject

    welcome_card.place_forget()
    result_card.place(relx=0.5, rely=0.52, anchor="center", relwidth=0.86, relheight=0.84)

    business_name_value.config(text=data["business_name"])
    phone_value.config(text=data["phone"])
    email_value.config(text=data["email"])
    address_value.config(text=data["address"])
    service_area_value.config(text=data["service_area"])
    opening_hours_value.config(text=data["opening_hours"])
    rating_value.config(text=data["rating"])
    business_type_value.config(text=data["business_type"])
    description_value.config(text=data["description"])

    fill_text_widget(services_box, list_to_text(data["services"], "No services found"))
    fill_text_widget(categories_box, list_to_text(data["categories"], "No categories found"))
    fill_text_widget(target_customers_box, list_to_text(data["target_customers"], "No target customers found"))
    fill_text_widget(usp_box, list_to_text(data["usp"], "No unique selling points found"))
    fill_text_widget(needs_verification_box, list_to_text(data["needs_verification"], "No verification notes"))
    fill_text_widget(missing_fields_box, list_to_text(data["missing_fields"], "No missing fields"))
    fill_text_widget(onboarding_box, onboarding_script)
    set_text_widget(email_box, f"Subject: {email_subject}\n\n{email_body}", editable=False)

    change_button.config(state="normal")
    save_changes_button.config(state="disabled")
    send_button.config(state="normal")

    root.update_idletasks()
    result_canvas.configure(scrollregion=result_canvas.bbox("all"))
    result_canvas.yview_moveto(0)


def show_error_from_worker(error_text):
    status_label.config(text="")
    create_button.config(state="normal")
    messagebox.showerror("Error", error_text)


def show_result_from_worker(extracted_data, onboarding_script, email_subject, email_body, discovered_pages, external_source):
    status_label.config(text="")
    create_button.config(state="normal")

    show_result_screen(
        extracted_data,
        onboarding_script,
        email_subject,
        email_body,
        ", ".join(discovered_pages),
        external_source
    )


def process_customer_worker(website_source, external_source):
    try:
        print("STEP 1: crawling website")
        website_text, discovered_pages = crawl_site_from_main(website_source, max_pages=5)
        print("STEP 1 DONE")

        if not discovered_pages:
            root.after(0, lambda: show_error_from_worker(
                "Could not read the client website from the provided main page."
            ))
            return

        print("STEP 2: reading external source")
        external_html = read_source(external_source)
        external_text = extract_visible_text(external_html)
        print("STEP 2 DONE")

        website_html_combined = ""
        for page_source in discovered_pages:
            try:
                website_html_combined += "\n" + read_source(page_source)
            except Exception:
                pass

        print("STEP 3: extraction")
        extracted_data = extract_with_ai(
            website_text,
            external_text,
            website_html_combined,
            external_html
        )
        print("STEP 3 DONE")

        print("STEP 4: generating outputs")
        onboarding_script = generate_onboarding_script(extracted_data)
        email_subject, email_body = generate_onboarding_email(extracted_data)

        save_email_draft(email_subject, email_body)
        save_crm_payload(extracted_data, onboarding_script, email_subject, email_body, discovered_pages, external_source)
        save_to_crm(
            extracted_data,
            onboarding_script,
            email_subject,
            email_body,
            ", ".join(discovered_pages),
            external_source
        )

        print("STEP 5: showing result screen")
        root.after(
            0,
            lambda: show_result_from_worker(
                extracted_data,
                onboarding_script,
                email_subject,
                email_body,
                discovered_pages,
                external_source
            )
        )
        print("DONE")

    except Exception as e:
        traceback.print_exc()
        root.after(0, lambda: show_error_from_worker(f"Failed to process customer:\n{e}"))


def process_customer():
    website_source = website_entry.get().strip()
    external_source = external_entry.get().strip()

    if not website_source or not external_source:
        messagebox.showerror("Missing input", "Please enter both the client website source and the external source.")
        return

    create_button.config(state="disabled")

    if USE_DEMO_MODE:
        status_label.config(text="Processing in demo mode...")
    else:
        status_label.config(text="Processing with live AI...")

    root.update_idletasks()

    worker = threading.Thread(
        target=process_customer_worker,
        args=(website_source, external_source),
        daemon=True
    )
    worker.start()


def back_to_welcome():
    global CURRENT_BUSINESS_NAME, CURRENT_WEBSITE_SOURCE, CURRENT_EXTERNAL_SOURCE, CURRENT_EMAIL_SUBJECT

    CURRENT_BUSINESS_NAME = ""
    CURRENT_WEBSITE_SOURCE = ""
    CURRENT_EXTERNAL_SOURCE = ""
    CURRENT_EMAIL_SUBJECT = ""

    result_card.place_forget()
    place_welcome_card()


def on_result_frame_configure(event):
    result_canvas.configure(scrollregion=result_canvas.bbox("all"))


def on_canvas_configure(event):
    result_canvas.itemconfig(result_canvas_window, width=event.width)


def on_result_canvas_mousewheel(event):
    result_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    return "break"


def on_textbox_mousewheel(event):
    event.widget.yview_scroll(int(-1 * (event.delta / 120)), "units")
    return "break"


def bind_mousewheel_to_main_area(widget):
    widget.bind("<MouseWheel>", on_result_canvas_mousewheel)


def on_global_mousewheel(event):
    widget_under_mouse = root.winfo_containing(event.x_root, event.y_root)

    current = widget_under_mouse
    while current is not None:
        if isinstance(current, tk.Text):
            current.yview_scroll(int(-1 * (event.delta / 120)), "units")
            return "break"
        current = current.master

    result_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    return "break"


def resize_bg(event=None):
    global bg_photo
    w = root.winfo_width()
    h = root.winfo_height()

    if w < 2 or h < 2:
        return

    resized = bg_image_original.resize((w, h), Image.LANCZOS)
    bg_photo = ImageTk.PhotoImage(resized)
    bg_label.config(image=bg_photo)


root = tk.Tk()
root.title("Zap Customer Onboarding Prototype")
root.geometry("1250x850")
root.minsize(1050, 720)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

init_db()
ensure_db_columns()

# ---------------- COLORS ----------------
CARD_BG = "#10203a"
CARD_BORDER = "#31486d"
TEXT_MAIN = "#eef4ff"
TEXT_SOFT = "#d2def0"
ACCENT = "#78c9ff"
ACCENT_HOVER = "#9ddcff"
ENTRY_BG = "#233858"
BOX_BG = "#1b2940"

# ---------------- STYLE ----------------
style = ttk.Style()
style.theme_use("clam")

style.configure(
    "Modern.TLabel",
    background=CARD_BG,
    foreground=TEXT_MAIN,
    font=("Segoe UI", 10)
)

style.configure(
    "Subtitle.TLabel",
    background=CARD_BG,
    foreground=TEXT_SOFT,
    font=("Segoe UI", 10)
)

style.configure(
    "Modern.TButton",
    font=("Segoe UI", 10, "bold"),
    padding=8,
    foreground="white",
    background=ACCENT,
    borderwidth=0,
    focusthickness=0
)

style.map(
    "Modern.TButton",
    background=[("active", ACCENT_HOVER)],
    foreground=[("active", "white")]
)

style.configure(
    "Modern.TEntry",
    fieldbackground=ENTRY_BG,
    background=ENTRY_BG,
    foreground="white",
    bordercolor=CARD_BORDER,
    lightcolor=CARD_BORDER,
    darkcolor=CARD_BORDER,
    insertcolor="white",
    padding=6
)

style.configure(
    "Vertical.TScrollbar",
    gripcount=0,
    background="#44536d",
    darkcolor="#44536d",
    lightcolor="#44536d",
    troughcolor="#101826",
    bordercolor="#101826",
    arrowcolor="white"
)

# ---------------- BACKGROUND IMAGE ----------------
bg_image_original = Image.open(os.path.join(BASE_DIR, "ZCA.png"))
bg_photo = None

bg_label = tk.Label(root, bd=0, highlightthickness=0)
bg_label.place(relx=0, rely=0, relwidth=1, relheight=1)

root.bind("<Configure>", resize_bg)
resize_bg()

# ---------------- WELCOME CARD ----------------
welcome_card = tk.Frame(
    root,
    bg=CARD_BG,
    highlightbackground=CARD_BORDER,
    highlightthickness=1,
    bd=0
)

place_welcome_card()

welcome_inner = tk.Frame(welcome_card, bg=CARD_BG)
welcome_inner.pack(fill="both", expand=True, padx=18, pady=12)

welcome_text = ttk.Label(
    welcome_inner,
    text="Enter the client website main page and the Dapei Zahav listing.\nThe system will scan internal pages automatically.",
    style="Subtitle.TLabel",
    justify="center"
)
welcome_text.pack(pady=(0, 10))

mode_label = tk.Label(
    welcome_inner,
    text="Mode: Demo (no API key detected)" if USE_DEMO_MODE else "Mode: Live AI",
    bg=CARD_BG,
    fg=TEXT_SOFT,
    font=("Segoe UI", 9, "italic")
)
mode_label.pack(pady=(0, 10))

website_label = ttk.Label(
    welcome_inner,
    text="Client website URL / file path",
    style="Modern.TLabel"
)
website_label.pack(anchor="w", pady=(2, 3))

website_entry = ttk.Entry(welcome_inner, width=80, style="Modern.TEntry")
website_entry.pack(fill="x", pady=(0, 8), ipady=2)
website_entry.insert(0, "index.html")

external_label = ttk.Label(
    welcome_inner,
    text="Dapei Zahav page URL / file path",
    style="Modern.TLabel"
)
external_label.pack(anchor="w", pady=(2, 3))

external_entry = ttk.Entry(welcome_inner, width=80, style="Modern.TEntry")
external_entry.pack(fill="x", pady=(0, 10), ipady=2)
external_entry.insert(0, "dapei_zahav_listing.html")

create_button = ttk.Button(
    welcome_inner,
    text="Create Client Card",
    style="Modern.TButton",
    command=process_customer
)
create_button.pack(pady=(0, 8))

status_label = tk.Label(
    welcome_inner,
    text="",
    bg=CARD_BG,
    fg=ACCENT,
    font=("Segoe UI", 10, "bold")
)
status_label.pack()

# ---------------- RESULT CARD ----------------
result_card = tk.Frame(
    root,
    bg=CARD_BG,
    highlightbackground=CARD_BORDER,
    highlightthickness=1
)

result_container = tk.Frame(result_card, bg=CARD_BG)
result_container.pack(fill="both", expand=True, padx=20, pady=20)

result_canvas = tk.Canvas(
    result_container,
    bg=CARD_BG,
    highlightthickness=0,
    bd=0
)
result_scrollbar = ttk.Scrollbar(
    result_container,
    orient="vertical",
    command=result_canvas.yview
)
result_canvas.configure(yscrollcommand=result_scrollbar.set)

result_scrollbar.pack(side="right", fill="y")
result_canvas.pack(side="left", fill="both", expand=True)

result_frame = tk.Frame(result_canvas, bg=CARD_BG)
result_canvas_window = result_canvas.create_window((0, 0), window=result_frame, anchor="nw")

result_frame.bind("<Configure>", on_result_frame_configure)
result_canvas.bind("<Configure>", on_canvas_configure)

bind_mousewheel_to_main_area(result_canvas)
bind_mousewheel_to_main_area(result_frame)

result_title = tk.Label(
    result_frame,
    text="Client Card",
    bg=CARD_BG,
    fg="white",
    font=("Segoe UI", 22, "bold")
)
result_title.pack(pady=(0, 20))


def add_info_row(parent, label_text, row):
    label = tk.Label(
        parent,
        text=label_text,
        bg=CARD_BG,
        fg=ACCENT,
        font=("Segoe UI", 10, "bold")
    )
    label.grid(row=row, column=0, sticky="nw", padx=6, pady=6)

    value = tk.Label(
        parent,
        text="",
        bg=CARD_BG,
        fg=TEXT_MAIN,
        wraplength=800,
        justify="left",
        font=("Segoe UI", 10)
    )
    value.grid(row=row, column=1, sticky="nw", padx=6, pady=6)
    return value


info_frame = tk.Frame(result_frame, bg=CARD_BG)
info_frame.pack(fill="x", pady=5)

business_name_value = add_info_row(info_frame, "Business Name:", 0)
phone_value = add_info_row(info_frame, "Phone:", 1)
email_value = add_info_row(info_frame, "Email:", 2)
address_value = add_info_row(info_frame, "Address:", 3)
service_area_value = add_info_row(info_frame, "Service Area:", 4)
opening_hours_value = add_info_row(info_frame, "Opening Hours:", 5)
rating_value = add_info_row(info_frame, "Rating:", 6)
business_type_value = add_info_row(info_frame, "Business Type:", 7)
description_value = add_info_row(info_frame, "Description:", 8)


def make_text_box(parent, title, height, width_chars=88):
    section_wrapper = tk.Frame(parent, bg=CARD_BG)
    section_wrapper.pack(fill="x", pady=(0, 2))

    title_label = tk.Label(
        section_wrapper,
        text=title,
        bg=CARD_BG,
        fg=ACCENT,
        font=("Segoe UI", 11, "bold")
    )
    title_label.pack(anchor="w", pady=(14, 6))

    box = tk.Text(
        section_wrapper,
        width=width_chars,
        height=height,
        wrap=tk.WORD,
        bg=BOX_BG,
        fg=TEXT_MAIN,
        insertbackground="white",
        relief="flat",
        bd=0,
        font=("Consolas", 10),
        padx=12,
        pady=10
    )
    box.pack(anchor="w", pady=(0, 10))
    box.bind("<MouseWheel>", on_textbox_mousewheel)

    box.config(state="disabled")
    return box


services_box = make_text_box(result_frame, "Services", 7, 88)
categories_box = make_text_box(result_frame, "Categories", 5, 88)
target_customers_box = make_text_box(result_frame, "Target Customers", 5, 88)
usp_box = make_text_box(result_frame, "Unique Selling Points", 5, 88)
needs_verification_box = make_text_box(result_frame, "Needs Verification", 6, 88)
missing_fields_box = make_text_box(result_frame, "Missing Fields", 5, 88)
onboarding_box = make_text_box(result_frame, "Onboarding Call Script", 12, 88)
email_box = make_text_box(result_frame, "Onboarding Email Draft", 14, 88)

email_buttons_frame = tk.Frame(result_frame, bg=CARD_BG)
email_buttons_frame.pack(fill="x", pady=(0, 10))

change_button = ttk.Button(
    email_buttons_frame,
    text="Change",
    style="Modern.TButton",
    command=enable_email_edit
)
change_button.pack(side="left", padx=(0, 8))

save_changes_button = ttk.Button(
    email_buttons_frame,
    text="Save Changes",
    style="Modern.TButton",
    command=save_email_changes
)
save_changes_button.pack(side="left", padx=(0, 8))
save_changes_button.config(state="disabled")

send_button = ttk.Button(
    email_buttons_frame,
    text="Send",
    style="Modern.TButton",
    command=send_email_simulation
)
send_button.pack(side="left")

back_button = ttk.Button(
    result_frame,
    text="Back",
    style="Modern.TButton",
    command=back_to_welcome
)
back_button.pack(pady=(8, 18))

root.bind_all("<MouseWheel>", on_global_mousewheel)

root.mainloop()