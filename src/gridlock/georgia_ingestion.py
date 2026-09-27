"""Georgia IRP ingestion, consolidated from the original PDF-prep/list notebooks."""

import re
import zipfile
import pandas as pd
import pymupdf as fitz
import requests
from gridlock.paths import RAW_DATA_DIR, PROCESSED_DATA_DIR

SOURCE_PAGE = "https://psc.ga.gov/search/facts-document/?documentId=221233"
SOURCE_URL = SOURCE_PAGE
ZIP_URL = "https://services.psc.ga.gov/api/v1/External/Public/Get/Document/DownloadFile/221233/102406"
RAW_DIR = RAW_DATA_DIR / "georgia_power" / "2025_irp"
ZIP_PATH = RAW_DIR / "2025_irp_public_disclosure.zip"
EXTRACT_DIR = RAW_DIR / "files"
# Reuse the existing selected PDFs; never overwrite these preserved working files.
OUTPUT_DIR = RAW_DIR / "selected"
PDF_PATH = OUTPUT_DIR / "01_expansion_plan_projects.pdf"


def download_volume3():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    if ZIP_PATH.exists():
        print("Reusing original ZIP:", ZIP_PATH)
    else:
        response = requests.get(ZIP_URL, timeout=180)
        response.raise_for_status()
        if not response.content.startswith(b"PK"):
            raise ValueError("The download did not return a ZIP file.")
        with ZIP_PATH.open("xb") as file:
            file.write(response.content)
        print("Downloaded:", ZIP_PATH)

    if not zipfile.is_zipfile(ZIP_PATH):
        raise ValueError("The saved file is not a valid ZIP archive.")
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ZIP_PATH, "r") as archive:
        matches = [item for item in archive.infolist() if not item.is_dir()
                   and item.filename.replace("\\", "/").split("/")[-1].lower()
                   == "2025 irp volume 3 public disclosure.pdf"]
        if len(matches) != 1:
            raise ValueError("Expected exactly one 2025 IRP Volume 3 PUBLIC DISCLOSURE PDF.")
        item = matches[0]
        VOLUME3_PATH = EXTRACT_DIR / "2025 IRP Volume 3 PUBLIC DISCLOSURE.pdf"
        contents = archive.read(item)
        if VOLUME3_PATH.exists():
            if VOLUME3_PATH.read_bytes() != contents:
                raise ValueError("Existing Volume 3 differs from the original ZIP.")
        else:
            with VOLUME3_PATH.open("xb") as file:
                file.write(contents)

    print("Volume 3:", VOLUME3_PATH)
    return VOLUME3_PATH


def find_section_ranges(VOLUME3_PATH):
    with fitz.open(VOLUME3_PATH) as document:
        plan_starts = []
        for level, title, page_number in document.get_toc():
            if "GA ITS Ten Year Plan" in title:
                plan_starts.append(page_number - 1)
        if len(plan_starts) != 1:
            raise ValueError("Could not identify one Georgia ITS Ten-Year Plan bookmark.")
        plan_start = plan_starts[0]
        first_page_text = document[plan_start].get_text("text")
        if "GA ITS Ten-Year Plan" not in first_page_text:
            raise ValueError("The bookmark does not point to the expected plan.")
        page_label = re.search(r"Page\s+1\s+of\s+(\d+)", first_page_text)
        if not page_label:
            raise ValueError("The plan's first printed page label was not found.")
        plan_page_count = int(page_label.group(1))
        plan_texts = []
        for index in range(plan_start, plan_start + plan_page_count):
            plan_texts.append(document[index].get_text("text"))

    print("Plan starts at Volume 3 PDF page:", plan_start + 1)
    print("Pages in the plan:", plan_page_count)
    toc_entries = []
    toc_pdf_pages = []
    printed_pages = {}

    for index, text in enumerate(plan_texts):
        label = re.search(r"Page\s+(\d+)\s+of\s+(\d+)", text)
        if not label or int(label.group(2)) != plan_page_count:
            raise ValueError("Missing or unexpected page label at plan index " + str(index))
        printed_page = int(label.group(1))
        if printed_page in printed_pages:
            raise ValueError("Duplicate printed page label: " + str(printed_page))
        printed_pages[printed_page] = plan_start + index

        # The plan's contents occupies its opening pages, before the body sections.
        if index < 5:
            found_entries = False
            for line in text.splitlines():
                entry = re.match(r"^\s*(.*?)\.{2,}\s*(\d+)\s*$", line)
                if entry:
                    toc_entries.append((entry.group(1).strip(), int(entry.group(2))))
                    found_entries = True
            if found_entries:
                toc_pdf_pages.append(plan_start + index + 1)

    if not toc_entries:
        raise ValueError("No table-of-contents entries were found.")
    if sorted(printed_pages) != list(range(1, plan_page_count + 1)):
        raise ValueError("The printed plan page labels are not complete.")

    print("Table of contents found on Volume 3 PDF pages:", toc_pdf_pages)

    sections = [
        ("Georgia ITS 10 Year Expansion Plan Projects List", "Cancelled Projects List", "01_expansion_plan_projects.pdf"),
        ("Stability Project Details", "Short Circuit Project Details", "02_stability_projects.pdf"),
        ("Short Circuit Project Details", "Interface Transfer Capability Project Details", "03_short_circuit_projects.pdf"),
        ("Interface Transfer Capability Project Details", "Steady State Project Details", "04_interface_transfer_projects.pdf"),
        ("Steady State Project Details", "Expansion Generation Units Details", "05_steady_state_projects.pdf"),
        ("Strategic Projects", "Fleet Transition Tables", "06_strategic_projects.pdf"),
    ]

    ranges = []
    for name, next_name, filename in sections:
        boundaries = []
        for heading in [name, next_name]:
            matches = []
            for toc_name, printed_page in toc_entries:
                if heading in toc_name:
                    matches.append(printed_page)
            if len(matches) != 1:
                raise ValueError("Expected one contents entry for: " + heading)
            printed_page = matches[0]
            pdf_index = printed_pages[printed_page]
            page_text = " ".join(plan_texts[pdf_index - plan_start].split())
            if heading not in page_text:
                raise ValueError("Heading was not verified on its page: " + heading)
            boundaries.append(printed_page)

        first_page = boundaries[0]
        last_page = boundaries[1] - 1
        if last_page < first_page:
            raise ValueError("Invalid section range: " + name)
        start_index = printed_pages[first_page]
        end_index = printed_pages[last_page]
        for page in range(first_page, last_page + 1):
            if printed_pages[page] != start_index + page - first_page:
                raise ValueError("Nonconsecutive pages in: " + name)
        ranges.append({
            "section": name,
            "plan_first": first_page,
            "plan_last": last_page,
            "start_index": start_index,
            "end_index": end_index,
            "output_path": OUTPUT_DIR / filename,
        })
        print(name, "| Plan pages:", first_page, "-", last_page,
              "| Volume 3 PDF pages:", start_index + 1, "-", end_index + 1)
    return ranges


def prepare_project_pdfs():
    volume_path = download_volume3()
    ranges = find_section_ranges(volume_path)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with fitz.open(volume_path) as original:
        for section in ranges:
            path = section["output_path"]
            expected_pages = section["end_index"] - section["start_index"] + 1
            if not path.exists():
                with fitz.open() as working:
                    working.insert_pdf(original, from_page=section["start_index"], to_page=section["end_index"])
                    working.save(path)
            with fitz.open(path) as saved:
                if len(saved) != expected_pages:
                    raise ValueError("Unexpected working PDF page count: " + str(path))
                # An old file must still correspond to the verified section, not just its page count.
                for offset, page in enumerate(saved):
                    if page.get_text() != original[section["start_index"] + offset].get_text():
                        raise ValueError("Working PDF differs from its original section: " + str(path))
    return pd.DataFrame(ranges)


def read_project_list():
    records = []
    page_counts = []

    with fitz.open(PDF_PATH) as document:
        for page_number, page in enumerate(document, start=1):
            label = re.search(r"Page\s+(\d+)\s+of\s+304", page.get_text("text"))
            if not label:
                raise ValueError("Missing printed plan page label.")
            tables = page.find_tables().tables
            if len(tables) != 1 or tables[0].col_count != 13:
                raise ValueError("Unexpected table layout on PDF page " + str(page_number))

            page_records = []
            for row in tables[0].extract():
                cells = []
                for cell in row:
                    cells.append(" ".join((cell or "").split()))
                if not any(cells):
                    continue
                if "TEAMS" in cells or "Zone" in cells or "Number" in cells or cells[0] == "Total":
                    continue

                name = " ".join(cells[3:6]).strip()
                name = " ".join(name.split())
                if cells[2].isdigit():
                    page_records.append({
                        "state": "GA",
                        "zone": cells[0],
                        "plan_year": cells[1],
                        "project_id": cells[2],
                        "project_name": name,
                        "need_date_raw": cells[6],
                        "project_sponsor": cells[7],
                        "estimated_cost_gpc_raw": cells[8],
                        "estimated_cost_gtc_raw": cells[9],
                        "estimated_cost_meag_raw": cells[10],
                        "estimated_cost_du_raw": cells[11],
                        "estimated_cost_total_raw": cells[12],
                        "source_file": PDF_PATH.name,
                        "source_pdf_page": page_number,
                        "source_plan_page": int(label.group(1)),
                        "source_url": SOURCE_URL,
                    })
                elif name and not any(cells[:3]) and not any(cells[6:]) and page_records:
                    page_records[-1]["project_name"] += " " + name
                else:
                    raise ValueError(f"Unrecognized row on PDF page {page_number}: {cells}")

            if not page_records:
                raise ValueError("No projects found on PDF page " + str(page_number))
            records.extend(page_records)
            page_counts.append({"source_pdf_page": page_number, "projects": len(page_records)})

    df_georgia = pd.DataFrame(records)
    return df_georgia


def read_descriptions():
    DETAIL_FILES = [
        ("02_stability_projects.pdf", "Stability"),
        ("03_short_circuit_projects.pdf", "Short circuit"),
        ("04_interface_transfer_projects.pdf", "Interface transfer capability"),
        ("05_steady_state_projects.pdf", "Steady state"),
    ]
    description_records = []
    skipped_pages = []

    for filename, section in DETAIL_FILES:
        with fitz.open(PDF_PATH.parent / filename) as document:
            for page_number, page in enumerate(document, start=1):
                text = page.get_text("text", sort=True)
                project = re.search(r"^\s*Teams\s*#\s*(\d+)\s*$", text, re.I | re.M)
                if not project:
                    if page_number == 1 and "Project Details" in text:
                        reason = "Section introduction"
                    elif "Estimated Cost" in text and "ITS Assigned designation" in text and "Description" not in text:
                        reason = "Cost continuation; no description on this page"
                    else:
                        raise ValueError(f"Unexpected page without a TEAMS header: {filename}, page {page_number}")
                    skipped_pages.append({"file": filename, "page": page_number, "reason": reason})
                    continue

                description = re.search(
                    r"^\s*Description\s*\n(.*?)^\s*Supporting Statement\s*$",
                    text, re.S | re.M,
                )
                label = re.search(r"Page\s+(\d+)\s+of\s+304", text)
                if not description or not description.group(1).strip() or not label:
                    raise ValueError(f"Missing description or page label: {filename}, page {page_number}")
                description_records.append({
                    "project_id": project.group(1),
                    "description": " ".join(description.group(1).split()),
                    "description_section": section,
                    "description_source_file": filename,
                    "description_pdf_page": page_number,
                    "description_plan_page": int(label.group(1)),
                })

    df_descriptions = pd.DataFrame(description_records)
    assert df_descriptions["project_id"].is_unique, "Duplicate description IDs need review"
    return df_descriptions


def read_strategic_summaries():
    STRATEGIC_FILE = "06_strategic_projects.pdf"
    strategic_records = []

    with fitz.open(PDF_PATH.parent / STRATEGIC_FILE) as document:
        for page_number, page in enumerate(document, start=1):
            text = page.get_text("text", sort=True)
            label = re.search(r"Page\s+(\d+)\s+of\s+304", text)
            if not label:
                raise ValueError("Missing strategic plan page label")
            project = re.search(r"^\s*TEAMS\s+(\d+)\s*$", text, re.I | re.M)
            if project:
                summary = re.search(
                    r"^\s*Executive Summary\s*\n(.*?)^\s*(?:Figure\s+\d+|Compliance Statement|Background and Problem Description)",
                    text, re.S | re.M,
                )
                if not summary or not summary.group(1).strip():
                    raise ValueError("Could not read strategic summary on page " + str(page_number))
                strategic_records.append({
                    "project_id": project.group(1),
                    "strategic_summary": " ".join(summary.group(1).split()),
                    "strategic_source_file": STRATEGIC_FILE,
                    "strategic_pdf_first_page": page_number,
                    "strategic_plan_first_page": int(label.group(1)),
                })
            if not strategic_records:
                raise ValueError("Strategic PDF starts without a project header")
            strategic_records[-1]["strategic_pdf_last_page"] = page_number
            strategic_records[-1]["strategic_plan_last_page"] = int(label.group(1))

    df_strategic = pd.DataFrame(strategic_records)
    assert df_strategic["project_id"].is_unique, "Duplicate strategic IDs need review"
    return df_strategic


def join_project_details(df_georgia, df_descriptions, df_strategic):
    expected_count = len(df_georgia)
    df_georgia = df_georgia.copy()
    df_georgia["plan_year"] = pd.to_numeric(df_georgia["plan_year"], errors="raise").astype(int)
    df_georgia["need_date"] = pd.to_datetime(df_georgia["need_date_raw"], format="%m/%d/%Y", errors="coerce")

    missing = df_georgia[~df_georgia["project_id"].isin(df_descriptions["project_id"])]
    extra = df_descriptions[~df_descriptions["project_id"].isin(df_georgia["project_id"])]
    extra_strategic = df_strategic[~df_strategic["project_id"].isin(df_georgia["project_id"])]
    print("List IDs without descriptions:", missing["project_id"].tolist())
    print("Description IDs outside the list:", extra["project_id"].tolist())
    print("Strategic IDs outside the list:", extra_strategic["project_id"].tolist())
    assert missing.empty and extra.empty and extra_strategic.empty, "Unmatched IDs need review before export"

    df_georgia = df_georgia.merge(df_descriptions, on="project_id", how="left", validate="one_to_one")
    df_georgia = df_georgia.merge(df_strategic, on="project_id", how="left", validate="one_to_one")
    assert len(df_georgia) == expected_count
    assert df_georgia["description"].notna().all()
    for column in ["strategic_pdf_first_page", "strategic_pdf_last_page", "strategic_plan_first_page", "strategic_plan_last_page"]:
        df_georgia[column] = df_georgia[column].astype("Int64")

    df_gpc = df_georgia[df_georgia["project_sponsor"] == "GPC"].copy()
    df_gpc["utility"] = "GPC"
    return df_georgia, df_gpc


def save_project_tables(df_georgia, df_gpc):
    folder = PROCESSED_DATA_DIR / "georgia_power" / "2025_irp"
    folder.mkdir(parents=True, exist_ok=True)
    df_georgia.to_csv(folder / "georgia_its_project_list.csv", index=False, date_format="%Y-%m-%d")
    df_gpc.to_csv(folder / "georgia_power_project_list.csv", index=False, date_format="%Y-%m-%d")
    return folder
