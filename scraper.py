import requests
from bs4 import BeautifulSoup
import json
import re
import io
from datetime import datetime
from fontTools.ttLib import TTFont

CLUB_ID = "00ES8GNK2800001CVV0AG08LVUPGND5I"
BASE = "https://www.fussball.de"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "de-DE,de;q=0.9",
    "Referer": "https://www.fussball.de/",
}

NEXT_URL = f"{BASE}/ajax.club.next.games/-/id/{CLUB_ID}"
PREV_URL = f"{BASE}/ajax.club.prev.games/-/id/{CLUB_ID}"

GLYPH_NAMES = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def build_decode_map(soup):
    """Extract obfuscation key from the page and download the per-key font to decode scores."""
    key_el = soup.find(attrs={"data-obfuscation": True})
    if not key_el:
        return {}
    key = key_el["data-obfuscation"]

    woff_url = f"{BASE}/export.fontface/-/format/woff/id/{key}/type/font"
    try:
        rw = requests.get(woff_url, headers=HEADERS, timeout=15)
        if rw.status_code != 200 or len(rw.content) < 100:
            return {}
        font = TTFont(io.BytesIO(rw.content))
        cmap = font.getBestCmap()
        if not cmap:
            return {}
        decode = {}
        for cp, glyph_name in cmap.items():
            digit = GLYPH_NAMES.get(glyph_name)
            if digit is not None:
                decode[cp] = digit
        print(f"  Loaded decode map for key '{key}': {len(decode)} entries")
        return decode
    except Exception as e:
        print(f"  Could not build decode map: {e}")
        return {}


def decode_text(text, decode_map):
    return "".join(decode_map.get(ord(ch), ch) for ch in text)


def try_get_score(row, decode_map):
    """Extract score from a match row using the font decode map."""
    left_span = row.find("span", class_="score-left")
    right_span = row.find("span", class_="score-right")
    if not left_span or not right_span:
        return None

    left = decode_text(left_span.get_text(), decode_map).strip()
    right = decode_text(right_span.get_text(), decode_map).strip()

    if left.isdigit() and right.isdigit():
        return f"{left}:{right}"
    return None


def parse_headline(text):
    """Extract date, time, competition from a row-headline cell text."""
    date_m = re.search(r"(\d{2}\.\d{2}\.\d{4})", text)
    time_m = re.search(r"(\d{2}:\d{2})\s*Uhr", text)
    parts = [p.strip() for p in text.split("|")]
    liga = parts[-1] if len(parts) >= 2 else ""
    return (
        date_m.group(1) if date_m else "",
        time_m.group(1) if time_m else "",
        liga,
    )


def parse_games(soup, decode_map):
    games = []
    rows = soup.find_all("tr")

    i = 0
    while i < len(rows):
        row = rows[i]
        classes = row.get("class", [])

        if "row-headline" in classes:
            td = row.find("td")
            if not td:
                i += 1
                continue

            date, time_val, liga = parse_headline(td.get_text(" ", strip=True))
            if not date:
                i += 1
                continue

            j = i + 1
            while j < len(rows):
                next_row = rows[j]
                if "row-headline" in next_row.get("class", []):
                    break

                club_divs = next_row.find_all("div", class_="club-name")
                if len(club_divs) >= 2:
                    home = club_divs[0].get_text(strip=True)
                    away = club_divs[1].get_text(strip=True)

                    game = {
                        "date": date,
                        "time": time_val,
                        "home": home,
                        "away": away,
                        "liga": liga,
                    }

                    score = try_get_score(next_row, decode_map)
                    if score:
                        game["score"] = score

                    games.append(game)

                j += 1

            i = j
        else:
            i += 1

    return games


def determine_result(game):
    if "score" not in game:
        return None
    home_is_sck = "SC Kreuz" in game["home"] or "Kreuz Bayreuth" in game["home"]
    away_is_sck = "SC Kreuz" in game["away"] or "Kreuz Bayreuth" in game["away"]
    goals = game["score"].split(":")
    if len(goals) != 2:
        return None
    try:
        g_home, g_away = int(goals[0]), int(goals[1])
    except ValueError:
        return None
    if home_is_sck:
        if g_home > g_away: return "win"
        if g_home < g_away: return "loss"
        return "draw"
    if away_is_sck:
        if g_away > g_home: return "win"
        if g_away < g_home: return "loss"
        return "draw"
    return "draw"


print("Fetching next games...")
next_soup = fetch(NEXT_URL)
next_decode = build_decode_map(next_soup)
next_games = parse_games(next_soup, next_decode)
print(f"  Found {len(next_games)} upcoming games")

print("Fetching previous games...")
prev_soup = fetch(PREV_URL)
prev_decode = build_decode_map(prev_soup)
prev_games = parse_games(prev_soup, prev_decode)
for g in prev_games:
    g["result"] = determine_result(g)
print(f"  Found {len(prev_games)} previous games")

data = {
    "updated": datetime.now().strftime("%d.%m.%Y %H:%M"),
    "nextGames": next_games[:6],
    "prevGames": prev_games[:6],
}

output = f"const FOOTBALL_DATA = {json.dumps(data, ensure_ascii=False, indent=2)};"
with open("football_data.js", "w", encoding="utf-8") as f:
    f.write(output)

print(f"Saved football_data.js (updated: {data['updated']})")
