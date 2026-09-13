import re
import json
import subprocess
import logging

logger = logging.getLogger(__name__)

def run_firecrawl_scrape(url):
    """
    Invokes Firecrawl CLI to scrape the page content and images.
    """
    try:
        cmd = ["firecrawl", "scrape", url, "-f", "markdown,images", "--json"]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=45)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            # data can be an object or array
            if isinstance(data, list) and len(data) > 0:
                data = data[0]
            markdown = data.get("markdown", "")
            images = data.get("images", [])
            metadata = data.get("metadata", {})
            return markdown, images, metadata
        else:
            logger.error(f"Firecrawl scrape failed: {result.stderr}")
    except Exception as e:
        logger.error(f"Firecrawl error: {e}")
    return "", [], {}

def detect_source(url):
    url_lower = url.lower()
    if "otodom.pl" in url_lower:
        return "otodom"
    elif "olx.pl" in url_lower:
        return "olx"
    return "other"

def parse_listing_content(url, markdown, images=None, metadata=None):
    images = images or []
    metadata = metadata or {}
    clean_url = url.split("?")[0].strip()
    text = markdown + "\n" + metadata.get("description", "") + "\n" + metadata.get("title", "")
    source = detect_source(clean_url)

    # 1. Title
    title = ""
    # Try finding real listing heading in markdown
    for line in markdown.splitlines():
        line_clean = line.strip()
        if line_clean.startswith("# ") and not any(k in line_clean.lower() for k in ["idź do", "otodom", "olx", "kupuję", "wynajmuję"]):
            candidate = line_clean.lstrip("# ").strip()
            if len(candidate) > 5 and "mieszkanie na wynajem" not in candidate.lower():
                title = candidate.replace("\\|", " | ").replace("|", " | ")
                title = re.sub(r"\s+", " ", title).strip()
                break

    if not title:
        title = metadata.get("title") or ""
    title = re.sub(r"\s*[\-•\|]\s*(?:Otodom\.pl|OLX\.pl).*$", "", title, flags=re.IGNORECASE).strip()
    title = re.sub(r"\s+Słupsk\s*$", "", title, flags=re.IGNORECASE).strip()
    if not title or len(title) < 5:
        title = "Квартира в Слупске"

    # 2. Base Price (Czynsz najmu / Odstępne / Cena)
    # Match patterns like "2 200 zł", "2200 PLN", "Cena: 2 500 zł/miesiąc", "czynsz dla właściciela - 2000zl"
    price_base = 0.0

    # Check OLX metadata description pattern: "2 000 zł: ..."
    desc_match = re.match(r"^\s*([1-9][0-9\s]{2,6})\s*(?:zł|pln|zl)\s*:", metadata.get("description", ""))
    if desc_match:
        val = float(desc_match.group(1).replace(" ", "").replace("\xa0", ""))
        if 800 <= val <= 2000000:
            price_base = val

    if price_base == 0.0:
        explicit_rent = re.search(r"(?:czynsz\s*(?:dla\s*właściciela|najmu)|najem|odstępne|cena\s*najmu)[:\s\-~]*([1-9][0-9\s]{2,5})\s*(?:zł|pln|zl)", text, re.IGNORECASE)
        if explicit_rent:
            val = float(explicit_rent.group(1).replace(" ", "").replace("\xa0", ""))
            if 800 <= val <= 15000:
                price_base = val

    if price_base == 0.0:
        # Check markdown header price "### 2 000 zł"
        h3_match = re.search(r"###\s*([1-9][0-9\s]{2,6})\s*(?:zł|pln|zl)", markdown, re.IGNORECASE)
        if h3_match:
            val = float(h3_match.group(1).replace(" ", "").replace("\xa0", ""))
            if 800 <= val <= 2000000:
                price_base = val

    if price_base == 0.0:
        for m in re.finditer(r"(?:cena|odstępne|koszt|wynajem)?[:\s]*([1-9][0-9\s]{2,6})\s*(?:zł|pln|zl)", text, re.IGNORECASE):
            # Check if this match is preceded by kaucja/depozyt/czynsz dodatkowo
            start = max(0, m.start() - 35)
            preceding = text[start:m.start()].lower()
            if any(k in preceding for k in ["kaucj", "depozyt", "czynsz", "dodatkowo", "spółdzieln"]):
                continue
            val = float(m.group(1).replace(" ", "").replace("\xa0", ""))
            # Reasonable rent in Słupsk: usually 1000 - 6000 PLN, or purchase: 150000+
            if 800 <= val <= 8000:
                price_base = val
                break
            elif val > 8000:
                # could be purchase price
                price_base = val
                break

    # 3. Czynsz administracyjny (opłaty do spółdzielni / wspólnoty)
    price_admin = 0.0
    admin_pattern = r"(?:czynsz\s*(?:\(?dodatkowo\)?|administracyjny|do\s*spółdzielni|na\s*rzecz\s*wspólnoty(?:\s*mieszkaniowej)?|opłaty\s*eksploatacyjne)?|opłat[ya]\s*(?:do\s*spółdzielni|administracyjn[ea]|eksploatacyjn[ea])?)[:\s\-~]*(?:wynosi|to|około|ok\.?)?\s*\(?\s*([1-9][0-9\s]{1,4})\s*(?:zł|pln)"
    for m in re.finditer(admin_pattern, text, re.IGNORECASE):
        val = float(m.group(1).replace(" ", "").replace("\xa0", ""))
        if 100 <= val <= 2500 and val != price_base:
            price_admin = val
            break

    if price_admin == 0.0:
        admin_pattern2 = r"czynsz\s*(?:\(dodatkowo\)|dodatkowo)?[:\s]*([1-9][0-9\s]{1,4})\s*(?:zł|pln)"
        for m in re.finditer(admin_pattern2, text, re.IGNORECASE):
            val = float(m.group(1).replace(" ", "").replace("\xa0", ""))
            if 100 <= val <= 2500 and val != price_base:
                price_admin = val
                break

    # 4. Kaucja (deposit)
    deposit = 0.0
    kaucja_match = re.search(r"(?:kaucja(?:\s*zwrotna)?|depozyt)[^\n\r]{0,60}?([1-9][0-9\s]{2,5}(?:[,\.][0-9]{2})?)\s*(?:zł|pln)", text, re.IGNORECASE)
    if kaucja_match:
        val = float(kaucja_match.group(1).replace(" ", "").replace("\xa0", "").replace(",", "."))
        if 500 <= val <= 15000:
            deposit = val
    elif price_base > 0 and re.search(r"kaucj[a-ząćęłńóśźż]*[^\n\r]{0,60}?(?:miesięczn|jednomiesięczn|1\s*mies)", text, re.IGNORECASE):
        deposit = price_base

    # 5. Area (Powierzchnia m²)
    area = 0.0
    area_match = re.search(r"([1-9][0-9]{1,2}(?:[,\.][0-9]{1,2})?)\s*(?:m²|m2|metrów|mkw)", text, re.IGNORECASE)
    if area_match:
        try:
            area = float(area_match.group(1).replace(",", "."))
        except ValueError:
            pass

    # 6. Rooms (Pokoje)
    rooms = 1
    rooms_match = re.search(r"(\d+)\s*(?:pok[oó]j|pokoi|pokoje)", text, re.IGNORECASE)
    if rooms_match:
        rooms = int(rooms_match.group(1))
    elif "kawalerka" in text.lower() or "1-pokoj" in text.lower() or "dwupokojowe" in text.lower() or "2-pokoj" in text.lower():
        if "dwupokojowe" in text.lower() or "2-pokoj" in text.lower():
            rooms = 2
        else:
            rooms = 1

    # 7. Floor (Piętro)
    floor = ""
    floor_match = re.search(r"(?:piętro|kondygnacja|poziom)[:\s]*([0-9]{1,2}(?:\s*/\s*[0-9]{1,2})?|parter|poddasze)", text, re.IGNORECASE)
    if floor_match:
        floor = floor_match.group(1).strip()
    elif re.search(r"\b(?:na\s+)?(?:1|I|pierwszym)\s+piętr(?:ze|o)\b", text_lower):
        floor = "1"
    elif re.search(r"\b(?:na\s+)?(?:2|II|drugim)\s+piętr(?:ze|o)\b", text_lower):
        floor = "2"
    elif re.search(r"\b(?:na\s+)?(?:3|III|trzecim)\s+piętr(?:ze|o)\b", text_lower):
        floor = "3"
    elif re.search(r"\b(?:na\s+)?(?:4|IV|czwartym)\s+piętr(?:ze|o)\b", text_lower):
        floor = "4"
    elif re.search(r"\bparter\b", text_lower):
        floor = "Parter"

    # 8. Heating (Ogrzewanie)
    heating = "nieznane"
    text_lower = text.lower()
    if any(k in text_lower for k in ["miejskie", "ogrzewanie miejskie", "c.o. miejskie", "z sieci miejskiej", "ciepło z sieci", "ogrzewanie z sieci"]):
        heating = "miejskie (centralne)"
    elif any(k in text_lower for k in ["gazowe", "piec gazowy", "piecyk gazowy", "ogrzewanie gazowe", "kocioł gazowy"]):
        heating = "gazowe"
    elif any(k in text_lower for k in ["elektryczne", "ogrzewanie elektryczne", "piece akumulacyjne", "panele na podczerwień", "grzejniki elektryczne"]):
        heating = "elektryczne ⚡"
    elif any(k in text_lower for k in ["kominek", "piec kaflowy", "węglowe", "na opał"]):
        heating = "piec / węgiel ⚠️"
    elif any(k in text_lower for k in ["czynszu spółdzielni", "opłata do spółdzielni", "spółdzielni", "wspólnoty", "wspólnota", "zaliczki na ogrzewanie"]):
        heating = "miejskie (wspólnota)"
    elif price_admin >= 500 and ("blok" in text_lower or "piętr" in text_lower):
        heating = "miejskie (spółdzielnia)"

    # 9. Balcony, Elevator, Parking, Piwnica
    has_balcony = 0
    if re.search(r"balkon[:\s]*nie|brak\s*balkon|bez\s*balkon", text_lower):
        has_balcony = 0
    elif any(k in text_lower for k in ["balkon", "balkonem", "loggia", "taras", "ogródek"]):
        has_balcony = 1

    has_elevator = 0
    if re.search(r"winda[:\s]*nie|brak\s*wind|bez\s*wind", text_lower):
        has_elevator = 0
    elif any(k in text_lower for k in ["winda: tak", "winda:tak", "winda", "windą"]):
        has_elevator = 1

    has_parking = 1 if any(k in text_lower for k in ["parking", "garaż", "miejsce postojowe", "miejsce parkingowe"]) else 0

    # 10. Contract Type (Typ umowy)
    contract_type = "zwykły"
    if any(k in text_lower for k in ["okazjonalny", "najem okazjonalny", "umowa okazjonalna", "notariusz", "oświadczenie o poddaniu się egzekucji"]):
        contract_type = "najem okazjonalny ⚠️"
    elif "instytucjonalny" in text_lower:
        contract_type = "najem instytucjonalny"

    # 11. Pets (Zwierzęta)
    pets_allowed = "не указано"
    if any(k in text_lower for k in ["zwierzęta: tak", "zwierzęta mile widziane", "zwierzęta akceptowane", "można ze zwierzętami", "przyjazne zwierzętom"]):
        pets_allowed = "да, разрешены 🐾"
    elif any(k in text_lower for k in ["zwierzęta: nie", "żadnych zwierząt", "bez zwierząt", "zwierzęta nie", "zakaz zwierząt", "nie akceptujemy zwierząt"]):
        pets_allowed = "нет, запрещено 🚫"

    # 12. District / Street in Słupsk
    district_configs = [
        ("Osiedle Batorego", [r"osiedle batorego", r"os\.?\s*batorego", r"oś\.?\s*batorego", r"\bbatorego\b"]),
        ("Osiedle Niepodległości", [r"osiedle niepodległości", r"oś\.?\s*niepodległości", r"os\.?\s*niepodległości", r"\bniepodległości\b"]),
        ("Zatorze", [r"\bzatorze\b", r"\bzatorzu\b"]),
        ("Westerplatte", [r"\bwesterplatte\b"]),
        ("Centrum", [r"\bcentrum\b", r"\bśródmieście\b", r"\bsrodmiescie\b"]),
        ("Ryczewo", [r"\bryczewo\b"]),
        ("Nadrzecze", [r"\bnadrzecze\b"]),
        ("Kobylnica", [r"\bkobylnica\b"]),
        ("Włynkówko", [r"\bwłynkówko\b", r"\bwlynkowko\b"]),
        ("Siemianice", [r"\bsiemianice\b"]),
        ("Redzikowo", [r"\bredzikow(?:o|a|ie)?\b"]),
    ]
    streets = [
        ("Chełmońskiego", r"\b(?:ul\.?|ulica)?\s*Che[łl]mo[ńn]sk(?:iego|i)?\b", "Osiedle Niepodległości"),
        ("Mochnackiego", r"\b(?:ul\.?|ulica)?\s*Mochnack(?:iego|i)?\b", "Osiedle Batorego"),
        ("Konarskiego", r"\b(?:ul\.?|ulica)?\s*Konarsk(?:iego|i)?\b", "Osiedle Batorego"),
        ("Szczecińska", r"\b(?:ul\.?|ulica)?\s*Szczecińsk(?:a|iej|ą|ie)?\b", "Zatorze / Os. Niepodległości"),
        ("Jagiełły", r"\b(?:ul\.?|ulica)?\s*Jagiełł(?:y)?\b", "Centrum"),
        ("Batorego", r"\b(?:ul\.?|ulica)?\s*Bator(?:ego|y)?\b", "Osiedle Batorego"),
        ("Hubalczyków", r"\b(?:ul\.?|ulica)?\s*Hubalczyk(?:ów)?\b", "Westerplatte"),
        ("Banacha", r"\b(?:ul\.?|ulica)?\s*Banach(?:a)?\b", "Zatorze"),
        ("Sobieskiego", r"\b(?:ul\.?|ulica)?\s*Sobiesk(?:iego|i)?\b", "Centrum"),
        ("Wojska Polskiego", r"\b(?:ul\.?|ulica)?\s*Wojska Polskiego\b", "Centrum"),
        ("Morska", r"\b(?:ul\.?|ulica)?\s*Morsk(?:a|iej|ą)?\b", "Północ / Wylot na Ustkę"),
        ("Ogrodowa", r"\b(?:ul\.?|ulica)?\s*Ogrodow(?:a|ej|ą)?\b", "Centrum"),
        ("Bogusława X", r"\b(?:ul\.?|ulica)?\s*Bogusława\s*X\b", "Centrum"),
        ("Anny Gryfitki", r"\b(?:ul\.?|ulica)?\s*Anny\s*Gryfitki\b", "Zatorze / Ryczewo"),
        ("Łady Cybulskiego", r"\b(?:ul\.?|ulica)?\s*Łady\s*Cybulskiego\b", "Zatorze"),
        ("Witosa", r"\b(?:ul\.?|ulica)?\s*Witosa\b", "Osiedle Niepodległości"),
        ("Romera", r"\b(?:ul\.?|ulica)?\s*Romera\b", "Osiedle Niepodległości"),
        ("11 Listopada", r"\b(?:ul\.?|ulica)?\s*11\s*Listopada\b", "Osiedle Niepodległości"),
        ("Gdyńska", r"\b(?:ul\.?|ulica)?\s*Gdyńsk(?:a|iej|ą)?\b", "Centrum"),
        ("Kilińskiego", r"\b(?:ul\.?|ulica)?\s*Kilińsk(?:iego|i)?\b", "Centrum"),
        ("Kołłątaja", r"\b(?:ul\.?|ulica)?\s*Kołłątaj(?:a)?\b", "Centrum"),
        ("Sienkiewicza", r"\b(?:ul\.?|ulica)?\s*Sienkiewicz(?:a)?\b", "Centrum"),
        ("Tuwima", r"\b(?:ul\.?|ulica)?\s*Tuwima\b", "Centrum"),
        ("Wileńska", r"\b(?:ul\.?|ulica)?\s*Wileńsk(?:a|iej|ą)?\b", "Centrum"),
        ("Konopnickiej", r"\b(?:ul\.?|ulica)?\s*Konopnickiej\b", "Centrum"),
        ("Kopernika", r"\b(?:ul\.?|ulica)?\s*Kopernik(?:a)?\b", "Centrum"),
        ("Lutosławskiego", r"\b(?:ul\.?|ulica)?\s*Lutosławsk(?:iego|i)?\b", "Centrum"),
        ("Zygmunta Augusta", r"\b(?:ul\.?|ulica|ulicy)?\s*(?:Zygmunta\s+Augusta|Z\.?\s*Augusta|(?:przy\s+)?ulicy\s+Augusta)\b", "Zatorze"),
        ("Poniatowskiego", r"\b(?:ul\.?|ulica)?\s*Poniatowsk(?:iego|i)?\b", "Centrum"),
        ("Grottgera", r"\b(?:ul\.?|ulica)?\s*Grottger(?:a)?\b", "Centrum"),
        ("Słowackiego", r"\b(?:ul\.?|ulica)?\s*Słowack(?:iego|i)?\b", "Centrum")
    ]
    detected_district = "Слупск"
    title_lower = title.lower()
    for d_name, d_patterns in district_configs:
        if any(re.search(p, title_lower) for p in d_patterns):
            detected_district = d_name
            break

    if detected_district == "Слупск":
        for d_name, d_patterns in district_configs:
            if d_name == "Centrum":
                if re.search(r"(?:w\s+centrum|samo\s+centrum|centrum\s+słupska)", text_lower) and not re.search(r"(?:do\s+centrum|blisko\s+centrum|dojazd\s+do\s+centrum)", text_lower):
                    detected_district = "Centrum"
                    break
            else:
                if any(re.search(p, text_lower) for p in d_patterns):
                    detected_district = d_name
                    break

    detected_street = ""
    for s_name, s_pat, s_dist in streets:
        if re.search(s_pat, text, re.IGNORECASE):
            detected_street = f"ul. {s_name}"
            if detected_district == "Слупск" and s_dist:
                detected_district = s_dist
            break

    if detected_street and detected_district != "Слупск":
        address = f"{detected_street}, {detected_district}, Słupsk"
    elif detected_street:
        address = f"{detected_street}, Słupsk"
    elif detected_district != "Слупск":
        address = f"{detected_district}, Słupsk"
    else:
        address = "Слупск"

    # 13. Estimate Media / Total Price
    # If czynsz already includes media (like in "media (czynsz do spółdzielni, energia, gaz) około 600"), don't double count
    media_included_in_admin = bool(re.search(r"media\s*\([^\)]*czynsz", text_lower))
    price_media = 0.0 if media_included_in_admin else (150.0 if price_base > 0 else 0.0)
    price_total = price_base + price_admin + price_media if price_base < 30000 else price_base

    # 14. Pros, Cons & Pitfalls Analysis (tailored to 6-month winter lease for a couple)
    pros = []
    cons = []
    pitfalls = []

    # Check Period / Lease Term
    if any(k in text_lower for k in ["do czerwca", "do maja", "do wakacji", "okres zimowy", "krótkoterminowy", "na kilka miesięcy"]):
        pros.append("🎯 Отличный вариант по сроку: сдача на зимний период / до лета (идеально для срока 6 месяцев)")
    elif any(k in text_lower for k in ["minimum rok", "min. 1 rok", "na minimum 12", "umowa na rok"]):
        pitfalls.append("⚠️ В объявлении указан минимум 1 год аренды — нужно сразу договариваться на 6 месяцев или право расторжения")

    # Check Heating (CRITICAL for Oct - Feb!)
    if "miejskie" in heating:
        pros.append("✅ Центральное городское отопление (miejskie) — максимальный комфорт и предсказуемые счета в холодный период (октябрь-февраль)")
    elif "gazowe" in heating:
        cons.append("Газовое отопление — зимой счета зависят от котла и утепления")
        pitfalls.append("⚠️ Газовый котел: спросить средние зимние счета за газ за прошлый год")
    elif "elektryczne" in heating:
        cons.append("❌ Электрическое отопление зимой")
        pitfalls.append("🚨 ОПАСНО ЗИМОЙ: Электроотопление с октября по февраль может удвоить коммунальные расходы (+800-1500 zł в месяц)")
    elif "piec" in heating:
        pitfalls.append("🚨 Печное отопление — зимой придется вручную топить, грязь и нестабильное тепло")

    # Check Contract
    if "okazjonalny" in contract_type:
        cons.append("Договор Najem okazjonalny (требует нотариуса)")
        pitfalls.append("⚠️ Najem okazjonalny: нужен польский адрес для выселения и визит к нотариусу (~400-500 zł)")
    else:
        pros.append("✅ Обычный договор (Najem zwykły, без нотариуса)")

    # Check Language / Nationality requirement
    if any(k in text_lower for k in ["znajomość j. polskiego", "znajomość języka polskiego", "język polski", "w języku polskim", "po polsku"]):
        if any(k in text_lower for k in ["wymagana", "minimalna", "tylko", "konieczna"]):
            pitfalls.append("⚠️ Требование владельца: минимальное знание польского языка (коммуникация на польском)")
    if any(k in text_lower for k in ["polskiej narodowości", "narodowości polskiej"]):
        pitfalls.append("⚠️ Предпочтение владельца: «polskiej narodowości» — необходимо первое сообщение писать на чистом польском языке (презентация аккуратной работающей пары)")

    # Check Park view
    if any(k in text_lower for k in ["widok na park", "widokiem na park", "pkiw"]):
        pros.append("🌳 Живописный вид на парк (PKiW), тихое зеленое место прямо у парка")

    # Check deposit installments
    if any(k in text_lower for k in ["dwie raty", "dwóch ratach", "2 raty"]):
        pros.append("Возможность разбить залог на 2 части (kaucja na 2 raty)")

    # Check Negotiation
    if "do negocjacji" in text_lower:
        pros.append("💬 Цена аренды обсуждаема (в объявлении: do negocjacji)")

    # Check Cellar / Storage
    if any(k in text_lower for k in ["piwnica", "piwnicą", "komórka"]):
        pros.append("Есть подвал (piwnica) для хранения вещей")

    # Check for couple / single
    if any(k in text_lower for k in ["dla pary", "dla 1 osoby lub pary", "dla singla lub pary"]):
        pros.append("🎯 Прямо подходит: собственник сдает 1 человеку или паре (bez dzieci)")

    if "zadaszony balkon" in text_lower:
        pros.append("Застекленный / крытый балкон")

    # Check Rooms for Couple
    if rooms >= 2:
        pros.append(f"✅ {rooms} комнаты — отлично для пары (личное пространство / спальня + кабинет)")
    else:
        cons.append("1 комната (одно общее пространство для двоих)")

    # Check Budget
    if price_total > 0:
        if price_total <= 2600:
            pros.append(f"💰 Отличная цена ({int(price_total)} zł/мес всё включено — с запасом укладывается в бюджет 3000)")
        elif price_total <= 3000:
            pros.append(f"👍 Укладывается в целевой бюджет до 3000 zł ({int(price_total)} zł/мес)")
        elif price_total <= 3500:
            cons.append(f"Близко к верхнему пределу бюджета: {int(price_total)} zł/мес")
        else:
            pitfalls.append(f"🚨 Превышает лимит бюджета ({int(price_total)} zł > 3500 zł)")

    # Check Balcony & Elevator
    if has_balcony:
        pros.append("Есть балкон / лоджия")

    if has_elevator:
        pros.append("Есть лифт")
    elif floor and any(f in floor for f in ["3", "4", "5", "poddasze"]):
        cons.append(f"Этаж {floor} без лифта")
    elif floor and "1" in floor:
        pros.append("1 этаж (удобно даже без лифта)")

    if has_parking:
        pros.append("Есть парковочные места у дома")

    # Check Czynsz
    if price_admin > 750:
        cons.append(f"Высокий административный czynsz ({int(price_admin)} zł/мес)")
    elif 0 < price_admin <= 600:
        pros.append(f"Умеренный czynsz + media ({int(price_admin)} zł/мес)")

    if re.search(r"\b1\s*os\b|\b1\s*osob", text_lower) and price_admin > 0:
        pitfalls.append(f"⚠️ Чинш spółdzielni {int(price_admin)} zł указан на 1 чел. — для пары (2 чел.) добавится ~80-120 zł (вода и мусор)")

    # Check Deposit
    if deposit > price_base * 1.5 and price_base > 0:
        pitfalls.append(f"Повышенный залог (kaucja {int(deposit)} zł)")

    # Check Agency
    contact_name = ""
    contact_phone = ""
    agent_match = (
        re.search(r"([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+\s+[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)\s*\((?:opiekun oferty|agent|doradca|pośrednik)\)", text, re.IGNORECASE) or
        re.search(r"\b(?:opiekun oferty|doradca|agent)[:\s\*\n]+([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+\s+[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)", text, re.IGNORECASE) or
        re.search(r"#### Opiekun oferty\s*\[\*\*([^\*\]]+)\*\*\]", markdown)
    )
    if agent_match:
        contact_name = agent_match.group(1).strip()
    else:
        olx_user = re.search(r"\*\*([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)\*\*\s*(?:\\*[\r\n\s]+)*Na OLX od", text, re.IGNORECASE)
        if olx_user:
            contact_name = olx_user.group(1).strip()

    is_private = any(k in text_lower for k in ["bez pośredników", "bez prowizji", "bezpośrednio", "od właściciela", "prywatne", "osoba prywatna"])
    has_realtor_markers = any(k in text_lower for k in ["prowizja", "pośrednik", "biuro nieruchomości", "wynagrodzenie biura", "wynagrodzenie pośrednika"])

    if is_private and not any(k in text_lower for k in ["prowizja", "wynagrodzenie biura", "biuro nieruchomości"]):
        pros.append("✅ Напрямую от владельца (без комиссии риелтора / prowizja 0 zł)")
    elif has_realtor_markers:
        if any(k in text_lower for k in ["jednomiesięczny", "jednorazowo"]):
            pitfalls.append(f"Комиссия агентства (prowizja) — 1 месяц аренды (~{int(price_base)} zł)")
        else:
            pitfalls.append("Объявление от риелтора — уточнить размер комиссии")

    # Building type
    building_type = "blok"
    if "kamienic" in text_lower:
        building_type = "kamienica"
        pitfalls.append("Каменица (старый фонд) — проверить звукоизоляцию, сырость и герметичность окон")
    elif re.search(r"now(y|ego|ym|ych)\s+(?:blok|budyn(?:ek|ku))|nowe\s+budownictwo|nowym\s+budownictwie|apartamentowiec|oddany\s+w\s+202|rok\s+budowy[:\s]*202", text_lower):
        building_type = "новый дом"
        pros.append("Современный дом / свежее строительство (2020+)")

    # Dishwasher
    if any(k in text_lower for k in ["zmywarka", "zmywarką", "zmywarkę"]):
        pros.append("Есть посудомоечная машина (zmywarka)")

    # Photos cleanup (filter small icons/trackers/svgs/logos/maps/avatars)
    clean_photos = []
    for img in images:
        img_lower = img.lower()
        if any(x in img_lower for x in [".svg", "logo", "icon", "avatar", "maps.googleapis", "staticmapservice", "img-resizer", "100x100"]) or "pagefooter" in img_lower:
            continue
        if "b6mm9szj" in img_lower:  # known agency logo
            continue
        if any(x in img for x in ["apollo", "ireland", "img-otodom", "img-olx", "olx-st", "apollo-ireland"]):
            if "image;s=" in img or img.endswith(('.jpg', '.jpeg', '.png')):
                # Upgrade low-res thumbnails to 1200px
                upgraded = re.sub(r";s=\d+x\d+", ";s=1200x0", img)
                clean_photos.append(upgraded)
        elif img.startswith("http") and not any(x in img_lower for x in ["static.olx", "statics.otodom"]):
            clean_photos.append(img)
    clean_photos = clean_photos[:15]

    return {
        "url": clean_url,
        "source": source,
        "title": title,
        "address": address,
        "district": detected_district,
        "deal_type": "rent" if price_base < 30000 else "buy",
        "price_base": price_base,
        "price_admin": price_admin,
        "price_media": price_media,
        "price_total": price_total,
        "deposit": deposit,
        "area": area,
        "rooms": rooms,
        "floor": floor,
        "building_type": building_type,
        "heating": heating,
        "has_balcony": has_balcony,
        "has_elevator": has_elevator,
        "has_parking": has_parking,
        "contract_type": contract_type,
        "pets_allowed": pets_allowed,
        "photos": clean_photos,
        "status": "new",
        "contact_name": contact_name,
        "contact_phone": contact_phone,
        "pros": pros,
        "cons": cons,
        "pitfalls": pitfalls,
        "notes": ""
    }

def analyze_and_extract_url(url):
    """
    Full pipeline: runs firecrawl on url, parses details, returns structured dict.
    """
    logger.info(f"Scraping {url} via firecrawl...")
    markdown, images, metadata = run_firecrawl_scrape(url)
    if not markdown and not metadata:
        raise ValueError("Не удалось получить данные со страницы. Проверьте ссылку.")
    return parse_listing_content(url, markdown, images, metadata)
