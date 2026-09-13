import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "apartments.db")

def get_db_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS apartments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT UNIQUE NOT NULL,
        source TEXT DEFAULT 'other',
        title TEXT NOT NULL,
        address TEXT DEFAULT '',
        district TEXT DEFAULT '',
        deal_type TEXT DEFAULT 'rent',
        price_base REAL DEFAULT 0,
        price_admin REAL DEFAULT 0,
        price_media REAL DEFAULT 0,
        price_total REAL DEFAULT 0,
        deposit REAL DEFAULT 0,
        area REAL DEFAULT 0,
        rooms INTEGER DEFAULT 1,
        floor TEXT DEFAULT '',
        building_type TEXT DEFAULT '',
        heating TEXT DEFAULT 'nieznane',
        has_balcony INTEGER DEFAULT 0,
        has_elevator INTEGER DEFAULT 0,
        has_parking INTEGER DEFAULT 0,
        contract_type TEXT DEFAULT 'nieznany',
        pets_allowed TEXT DEFAULT 'nieznane',
        photos TEXT DEFAULT '[]',
        status TEXT DEFAULT 'new',
        contact_name TEXT DEFAULT '',
        contact_phone TEXT DEFAULT '',
        viewing_date TEXT DEFAULT '',
        rating INTEGER DEFAULT 0,
        user_rating INTEGER DEFAULT 0,
        initial_rating INTEGER DEFAULT 0,
        pros TEXT DEFAULT '[]',
        cons TEXT DEFAULT '[]',
        pitfalls TEXT DEFAULT '[]',
        notes TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """)

    # Check and migrate columns if table was created earlier
    cursor.execute("PRAGMA table_info(apartments)")
    cols = [col[1] for col in cursor.fetchall()]
    if 'user_rating' not in cols:
        cursor.execute("ALTER TABLE apartments ADD COLUMN user_rating INTEGER DEFAULT 0")
    if 'initial_rating' not in cols:
        cursor.execute("ALTER TABLE apartments ADD COLUMN initial_rating INTEGER DEFAULT 0")
        cursor.execute("UPDATE apartments SET initial_rating = rating WHERE (initial_rating IS NULL OR initial_rating = 0) AND rating > 0")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS criteria (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        deal_type TEXT DEFAULT 'rent',
        max_budget REAL DEFAULT 3000,
        min_rooms INTEGER DEFAULT 2,
        min_area REAL DEFAULT 35,
        pets TEXT DEFAULT 'нет',
        contract_preference TEXT DEFAULT 'любой',
        preferred_districts TEXT DEFAULT '[]',
        must_haves TEXT DEFAULT '[]',
        deal_breakers TEXT DEFAULT '[]',
        notes TEXT DEFAULT '',
        updated_at TEXT NOT NULL
    )
    """)

    # Insert default criteria if not exists
    cursor.execute("SELECT id FROM criteria WHERE id = 1")
    if not cursor.fetchone():
        now = datetime.now().isoformat()
        cursor.execute("""
        INSERT INTO criteria (id, deal_type, max_budget, min_rooms, min_area, pets, contract_preference, preferred_districts, must_haves, deal_breakers, notes, updated_at)
        VALUES (1, 'rent', 3000, 2, 40, 'нет', 'обычный договор (najem zwykły)', '["Centrum", "Zatorze", "Osiedle Niepodległości"]', '["Центральное отопление (miejskie)", "Стиральная машина"]', '["Отопление электрическое/печное", "Высокий czynsz"]', '', ?)
        """, (now,))

    conn.commit()
    conn.close()

def calculate_apartment_score(apt, criteria=None):
    """
    Рассчитывает алгоритмический скоринг квартиры (0-100) и формирует прозрачную детализацию.
    Исключены: район, лифт, гараж/паркинг (по прямому требованию пользователя).
    Учитываются:
    1. Бюджет (полная стоимость vs max_budget)
    2. Административный чинш (czynsz administracyjny)
    3. Тип отопления (miejskie / gazowe / elektryczne / piec)
    4. Тип договора (najem zwykły vs okazjonalny)
    5. Размер залога (kaucja)
    6. Соответствие по площади и комнатам
    7. Выявленные подводные камни
    """
    if criteria is None:
        criteria = {}

    factors = []
    total = 0

    # 1. Бюджет (до 35 баллов)
    max_b = float(criteria.get('max_budget') or 3000)
    pt = float(apt.get('price_total') or (float(apt.get('price_base') or 0) + float(apt.get('price_admin') or 0) + float(apt.get('price_media') or 0)))
    if pt > 0:
        diff = max_b - pt
        if diff >= 500:
            p = 35
            lbl = f"Супер-цена: {int(pt)} zł (экономия {int(diff)} zł от бюджета {int(max_b)} zł)"
        elif diff >= 200:
            p = 30
            lbl = f"Выгодная цена: {int(pt)} zł (на {int(diff)} zł ниже лимита {int(max_b)} zł)"
        elif diff >= 0:
            p = 25
            lbl = f"В рамках бюджета: {int(pt)} zł (лимит {int(max_b)} zł)"
        elif diff >= -200:
            p = 10
            lbl = f"Небольшое превышение бюджета: {int(pt)} zł (+{int(-diff)} zł к лимиту)"
        elif diff >= -500:
            p = -10
            lbl = f"Превышение бюджета: {int(pt)} zł (+{int(-diff)} zł к лимиту)"
        else:
            p = -25
            lbl = f"Сильное превышение бюджета: {int(pt)} zł (+{int(-diff)} zł)"
    else:
        p = 15
        lbl = "Полная цена не указана / по запросу"
    factors.append({'label': lbl, 'points': p, 'type': 'pos' if p > 0 else ('neg' if p < 0 else 'neutral')})
    total += p

    # 2. Отопление (до 25 баллов)
    ht = (apt.get('heating') or '').lower()
    if 'miejsk' in ht:
        p = 25
        lbl = "Городское отопление (miejskie) — стабильно и безопасно"
    elif 'gazow' in ht:
        p = 15
        lbl = "Газовое отопление (автономный котел)"
    elif 'elektr' in ht:
        p = -25
        lbl = "⚠️ Электроотопление — риск больших счетов зимой"
    elif 'piec' in ht or 'węglow' in ht:
        p = -30
        lbl = "⚠️ Печное отопление — неудобно и холодно"
    else:
        p = 5
        lbl = f"Отопление: {apt.get('heating') or 'не уточнено'}"
    factors.append({'label': lbl, 'points': p, 'type': 'pos' if p > 0 else ('neg' if p < 0 else 'neutral')})
    total += p

    # 3. Административный чинш (Czynsz) (до 15 баллов)
    adm = float(apt.get('price_admin') or 0)
    if adm > 0:
        if adm <= 350:
            p = 15
            lbl = f"Низкий czynsz ({int(adm)} zł)"
        elif adm <= 550:
            p = 10
            lbl = f"Умеренный czynsz ({int(adm)} zł)"
        elif adm <= 750:
            p = 0
            lbl = f"Ощутимый czynsz ({int(adm)} zł)"
        else:
            p = -15
            lbl = f"⚠️ Очень высокий czynsz ({int(adm)} zł)"
    else:
        p = 5
        lbl = "Czynsz включен в аренду или 0 zł"
    factors.append({'label': lbl, 'points': p, 'type': 'pos' if p > 0 else ('neg' if p < 0 else 'neutral')})
    total += p

    # 4. Тип договора (до 10 баллов)
    ct = (apt.get('contract_type') or '').lower()
    if 'zwyk' in ct:
        p = 10
        lbl = "Обычный договор (zwykły, без нотариуса)"
    elif 'okazjon' in ct:
        p = -5
        lbl = "Договор okazjonalny (нужен нотариус и адрес в Польше)"
    else:
        p = 0
        lbl = "Тип договора: уточняется у владельца"
    factors.append({'label': lbl, 'points': p, 'type': 'pos' if p > 0 else ('neg' if p < 0 else 'neutral')})
    total += p

    # 5. Залог (Kaucja) (до 10 баллов)
    dep = float(apt.get('deposit') or 0)
    base = float(apt.get('price_base') or apt.get('price_total') or 2000)
    if dep > 0:
        if dep <= base:
            p = 10
            lbl = f"Стандартный залог ({int(dep)} zł = 1 месяц)"
        elif dep <= base * 1.5:
            p = 5
            lbl = f"Умеренный залог ({int(dep)} zł)"
        else:
            p = -10
            lbl = f"⚠️ Высокий залог ({int(dep)} zł > 1.5 мес. аренды)"
    else:
        p = 5
        lbl = "Залог не указан или 0 zł"
    factors.append({'label': lbl, 'points': p, 'type': 'pos' if p > 0 else ('neg' if p < 0 else 'neutral')})
    total += p

    # 6. Комнаты и площадь (до 10 баллов)
    r = int(apt.get('rooms') or 1)
    a = float(apt.get('area') or 0)
    min_r = int(criteria.get('min_rooms') or 2)
    min_a = float(criteria.get('min_area') or 35)
    if r >= min_r and (a == 0 or a >= min_a):
        p = 10
        lbl = f"Метраж и комнаты в норме ({r} комн., {a} м²)"
    elif r >= min_r and a < min_a:
        p = 0
        lbl = f"Площадь {a} м² чуть ниже желаемой ({min_a} м²)"
    elif r < min_r:
        p = -10
        lbl = f"Меньше комнат ({r} комн. при желаемых {min_r})"
    else:
        p = 5
        lbl = f"Параметры: {r} комн., {a} м²"
    factors.append({'label': lbl, 'points': p, 'type': 'pos' if p > 0 else ('neg' if p < 0 else 'neutral')})
    total += p

    # 7. Подводные камни (штраф до -15 баллов)
    pits = apt.get('pitfalls') or []
    if isinstance(pits, str):
        try:
            pits = json.loads(pits)
        except Exception:
            pits = []
    if len(pits) > 0:
        p = -min(len(pits) * 5, 15)
        lbl = f"Подводные камни (-{abs(p)} б. за {len(pits)} риска)"
        factors.append({'label': lbl, 'points': p, 'type': 'neg'})
        total += p

    clamped = max(0, min(100, total))
    return clamped, factors

def dict_from_row(row):
    d = dict(row)
    for json_field in ['photos', 'pros', 'cons', 'pitfalls']:
        if json_field in d and isinstance(d[json_field], str):
            try:
                d[json_field] = json.loads(d[json_field])
            except Exception:
                d[json_field] = []
    d['user_rating'] = int(d.get('user_rating') or 0)
    d['initial_rating'] = int(d.get('initial_rating') if d.get('initial_rating') is not None else d.get('rating', 0))
    return d

def get_all_apartments(status_filter=None, search=None, sort_by='created_at', sort_order='desc'):
    conn = get_db_connection()
    query = "SELECT * FROM apartments WHERE 1=1"
    params = []

    if status_filter and status_filter != 'all':
        query += " AND status = ?"
        params.append(status_filter)

    if search:
        query += " AND (title LIKE ? OR address LIKE ? OR district LIKE ? OR notes LIKE ?)"
        like_term = f"%{search}%"
        params.extend([like_term, like_term, like_term, like_term])

    allowed_sorts = {
        'created_at': 'created_at',
        'price_total': 'price_total',
        'price_base': 'price_base',
        'area': 'area',
        'rating': 'rating',
        'initial_rating': 'initial_rating',
        'user_rating': 'user_rating',
        'rooms': 'rooms'
    }
    col = allowed_sorts.get(sort_by, 'created_at')
    direction = 'ASC' if sort_order.lower() == 'asc' else 'DESC'
    query += f" ORDER BY {col} {direction}"

    cursor = conn.cursor()
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    criteria = get_criteria()
    results = []
    for r in rows:
        apt = dict_from_row(r)
        score, breakdown = calculate_apartment_score(apt, criteria)
        apt['score'] = score
        apt['score_breakdown'] = breakdown
        results.append(apt)

    if sort_by == 'score':
        results.sort(key=lambda x: x.get('score', 0), reverse=(direction == 'DESC'))
    elif sort_by == 'user_rating':
        results.sort(key=lambda x: x.get('user_rating', 0), reverse=(direction == 'DESC'))
    elif sort_by in ('rating', 'initial_rating'):
        results.sort(key=lambda x: x.get('initial_rating', x.get('rating', 0)), reverse=(direction == 'DESC'))

    return results

def get_apartment_by_id(apt_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM apartments WHERE id = ?", (apt_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    apt = dict_from_row(row)
    criteria = get_criteria()
    score, breakdown = calculate_apartment_score(apt, criteria)
    apt['score'] = score
    apt['score_breakdown'] = breakdown
    return apt

def get_apartment_by_url(url):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM apartments WHERE url = ?", (url,))
    row = cursor.fetchone()
    conn.close()
    return dict_from_row(row) if row else None

def add_or_update_apartment(data):
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    # Normalize fields
    photos = json.dumps(data.get('photos', []))
    pros = json.dumps(data.get('pros', []))
    cons = json.dumps(data.get('cons', []))
    pitfalls = json.dumps(data.get('pitfalls', []))

    price_base = float(data.get('price_base') or 0)
    price_admin = float(data.get('price_admin') or 0)
    price_media = float(data.get('price_media') or 0)
    price_total = float(data.get('price_total') or (price_base + price_admin + price_media))

    url = data.get('url', '').strip()
    existing = get_apartment_by_url(url) if url else None

    if existing:
        apt_id = existing['id']
        user_rating = int(data['user_rating']) if ('user_rating' in data and data['user_rating'] is not None) else int(existing.get('user_rating') or 0)
        initial_rating = int(data['initial_rating']) if ('initial_rating' in data and data['initial_rating'] is not None) else int(existing.get('initial_rating') or existing.get('rating') or 0)

        cursor.execute("""
        UPDATE apartments SET
            title = ?, address = ?, district = ?, deal_type = ?,
            price_base = ?, price_admin = ?, price_media = ?, price_total = ?, deposit = ?,
            area = ?, rooms = ?, floor = ?, building_type = ?, heating = ?,
            has_balcony = ?, has_elevator = ?, has_parking = ?,
            contract_type = ?, pets_allowed = ?, photos = ?,
            status = COALESCE(NULLIF(?, ''), status),
            contact_name = COALESCE(NULLIF(?, ''), contact_name),
            contact_phone = COALESCE(NULLIF(?, ''), contact_phone),
            viewing_date = COALESCE(NULLIF(?, ''), viewing_date),
            rating = COALESCE(NULLIF(?, 0), rating),
            user_rating = ?,
            initial_rating = ?,
            pros = ?, cons = ?, pitfalls = ?,
            notes = COALESCE(NULLIF(?, ''), notes),
            updated_at = ?
        WHERE id = ?
        """, (
            data.get('title', existing['title']),
            data.get('address', existing['address']),
            data.get('district', existing['district']),
            data.get('deal_type', existing['deal_type']),
            price_base, price_admin, price_media, price_total,
            float(data.get('deposit') or 0),
            float(data.get('area') or 0),
            int(data.get('rooms') or 1),
            str(data.get('floor') or ''),
            str(data.get('building_type') or ''),
            str(data.get('heating') or 'nieznane'),
            int(data.get('has_balcony') or 0),
            int(data.get('has_elevator') or 0),
            int(data.get('has_parking') or 0),
            str(data.get('contract_type') or 'nieznany'),
            str(data.get('pets_allowed') or 'nieznane'),
            photos,
            data.get('status'),
            data.get('contact_name'),
            data.get('contact_phone'),
            data.get('viewing_date'),
            int(data.get('rating') or 0),
            user_rating,
            initial_rating,
            pros, cons, pitfalls,
            data.get('notes'),
            now,
            apt_id
        ))
        conn.commit()
        conn.close()
        return apt_id
    else:
        user_rating = int(data.get('user_rating') or 0)
        initial_rating = int(data.get('initial_rating') or data.get('rating') or 0)

        cursor.execute("""
        INSERT INTO apartments (
            url, source, title, address, district, deal_type,
            price_base, price_admin, price_media, price_total, deposit,
            area, rooms, floor, building_type, heating,
            has_balcony, has_elevator, has_parking,
            contract_type, pets_allowed, photos,
            status, contact_name, contact_phone, viewing_date, rating,
            user_rating, initial_rating,
            pros, cons, pitfalls, notes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            url,
            data.get('source', 'other'),
            data.get('title', 'Квартира в Слупске'),
            data.get('address', ''),
            data.get('district', ''),
            data.get('deal_type', 'rent'),
            price_base, price_admin, price_media, price_total,
            float(data.get('deposit') or 0),
            float(data.get('area') or 0),
            int(data.get('rooms') or 1),
            str(data.get('floor') or ''),
            str(data.get('building_type') or ''),
            str(data.get('heating') or 'nieznane'),
            int(data.get('has_balcony') or 0),
            int(data.get('has_elevator') or 0),
            int(data.get('has_parking') or 0),
            str(data.get('contract_type') or 'nieznany'),
            str(data.get('pets_allowed') or 'nieznane'),
            photos,
            data.get('status', 'new'),
            data.get('contact_name', ''),
            data.get('contact_phone', ''),
            data.get('viewing_date', ''),
            int(data.get('rating') or 0),
            user_rating,
            initial_rating,
            pros, cons, pitfalls,
            data.get('notes', ''),
            now, now
        ))
        new_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return new_id

def update_apartment_field(apt_id, fields_dict):
    conn = get_db_connection()
    cursor = conn.cursor()
    fields_dict['updated_at'] = datetime.now().isoformat()
    
    # Handle json serialization if needed
    for jf in ['photos', 'pros', 'cons', 'pitfalls']:
        if jf in fields_dict and not isinstance(fields_dict[jf], str):
            fields_dict[jf] = json.dumps(fields_dict[jf])

    set_clause = ", ".join([f"{k} = ?" for k in fields_dict.keys()])
    values = list(fields_dict.values()) + [apt_id]

    cursor.execute(f"UPDATE apartments SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return True

def delete_apartment(apt_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM apartments WHERE id = ?", (apt_id,))
    conn.commit()
    conn.close()
    return True

def get_criteria():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM criteria WHERE id = 1")
    row = cursor.fetchone()
    conn.close()
    if not row:
        return {}
    d = dict(row)
    for jf in ['preferred_districts', 'must_haves', 'deal_breakers']:
        if jf in d and isinstance(d[jf], str):
            try:
                d[jf] = json.loads(d[jf])
            except Exception:
                d[jf] = []
    return d

def save_criteria(data):
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    
    pref_dist = json.dumps(data.get('preferred_districts', []))
    must_h = json.dumps(data.get('must_haves', []))
    deal_b = json.dumps(data.get('deal_breakers', []))

    cursor.execute("""
    UPDATE criteria SET
        deal_type = ?, max_budget = ?, min_rooms = ?, min_area = ?,
        pets = ?, contract_preference = ?, preferred_districts = ?,
        must_haves = ?, deal_breakers = ?, notes = ?, updated_at = ?
    WHERE id = 1
    """, (
        data.get('deal_type', 'rent'),
        float(data.get('max_budget') or 3000),
        int(data.get('min_rooms') or 1),
        float(data.get('min_area') or 30),
        str(data.get('pets', 'нет')),
        str(data.get('contract_preference', '')),
        pref_dist, must_h, deal_b,
        str(data.get('notes', '')),
        now
    ))
    conn.commit()
    conn.close()
    return True

def get_stats():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM apartments")
    total = cursor.fetchone()[0]

    cursor.execute("SELECT status, COUNT(*) FROM apartments GROUP BY status")
    by_status = dict(cursor.fetchall())

    cursor.execute("SELECT AVG(price_total), AVG(price_base), AVG(area) FROM apartments WHERE price_total > 0")
    avg_row = cursor.fetchone()
    avg_price_total = round(avg_row[0] or 0)
    avg_price_base = round(avg_row[1] or 0)
    avg_area = round(avg_row[2] or 0, 1)

    conn.close()
    return {
        "total": total,
        "by_status": by_status,
        "avg_price_total": avg_price_total,
        "avg_price_base": avg_price_base,
        "avg_area": avg_area
    }
