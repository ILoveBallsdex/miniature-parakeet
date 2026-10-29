import sqlite3
import os
import requests as http
from urllib.parse import urlencode
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from markupsafe import Markup
from config import load_config, save_config

app = Flask(__name__)
app.secret_key = 'futbot-dashboard-dev'

DB_PATH = r'C:\Users\abhir\OneDrive\Documents\VSCode\FutBot Activity\cards_game.db'

RARITIES = ['Common', 'Uncommon', 'Rare']

POSITIONS = ['GK', 'CB', 'LB', 'RB', 'CDM', 'CM', 'CAM', 'LM', 'RM', 'LW', 'RW', 'ST', 'CF']


# ── Local SQLite helpers ───────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn, name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def get_card_types(conn):
    rows = conn.execute(
        'SELECT DISTINCT card_type FROM cards WHERE card_type IS NOT NULL ORDER BY card_type'
    ).fetchall()
    types = [r[0] for r in rows if r[0]]
    for default in ('Standard', 'Icon', 'Hero', 'Normal', 'Euro TOTT', 'Copa America TOTT'):
        if default not in types:
            types.append(default)
    return types


def get_pack_cols(conn):
    return [r['name'] for r in conn.execute('PRAGMA table_info(packs)').fetchall()
            if r['name'] != 'user_id']


# ── Remote API helpers ─────────────────────────────────────────────────────────

class RemoteDB:
    """Thin HTTP wrapper around the bot's aiohttp API (Online mode)."""

    def __init__(self, api_url: str, api_key: str):
        self.base = api_url.rstrip('/')
        self._h   = {'X-API-Key': api_key, 'Content-Type': 'application/json'}

    def _get(self, path):
        r = http.get(f'{self.base}{path}', headers=self._h, timeout=10)
        r.raise_for_status()
        return r.json()

    def _post(self, path, data):
        r = http.post(f'{self.base}{path}', json=data, headers=self._h, timeout=10)
        r.raise_for_status()
        return r.json()

    def _del(self, path):
        r = http.delete(f'{self.base}{path}', headers=self._h, timeout=10)
        r.raise_for_status()
        return r.json()

    def tables(self):
        return self._get('/api/tables')['tables']

    def table_exists(self, name):
        return name in self.tables()

    def table_rows(self, table):
        """Returns list of dicts; each dict includes a 'rowid' key."""
        return self._get(f'/api/table/{table}')['rows']

    def table_columns(self, table):
        rows = self.table_rows(table)
        if not rows:
            return []
        return [k for k in rows[0] if k != 'rowid']

    def insert(self, table, data: dict) -> int:
        return self._post(f'/api/table/{table}/insert', data)['inserted_id']

    def update(self, table, rowid: int, data: dict):
        self._post(f'/api/table/{table}/update/{rowid}', data)

    def delete(self, table, rowid: int):
        self._del(f'/api/table/{table}/delete/{rowid}')


def is_online() -> bool:
    return load_config().get('mode') == 'online'


def _rdb() -> RemoteDB:
    cfg = load_config()
    return RemoteDB(cfg.get('api_url', ''), cfg.get('api_key', ''))


# ── Jinja helpers ──────────────────────────────────────────────────────────────

@app.context_processor
def inject_helpers():
    def sort_th(label, field, cur_sort, cur_order, *filter_vals, **extra):
        new_order = 'asc' if (cur_sort == field and cur_order == 'desc') else 'desc'
        params = dict(extra)
        filter_names = ['search', 'rarity', 'club', 'nation', 'league']
        for i, v in enumerate(filter_vals):
            if v:
                params[filter_names[i]] = v
        params['sort']  = field
        params['order'] = new_order
        qs = urlencode(params)
        active = 'active' if cur_sort == field else ''
        arrow  = ''
        if cur_sort == field:
            icon  = 'caret-down-fill' if cur_order == 'desc' else 'caret-up-fill'
            arrow = f'<i class="bi bi-{icon} ms-1"></i>'
        html = (f'<th><a href="?{qs}" class="th-sort {active}">'
                f'{label}{arrow}</a></th>')
        return Markup(html)
    return dict(sort_th=sort_th)


# ═══════════════════════════════════════════════════════════════════════════════
# CARDS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    return redirect(url_for('cards'))


@app.route('/cards')
def cards():
    search = request.args.get('search', '').strip()
    rarity = request.args.get('rarity', '')
    club   = request.args.get('club', '')
    nation = request.args.get('nation', '')
    league = request.args.get('league', '')
    sort   = request.args.get('sort', 'overall')
    order  = request.args.get('order', 'desc')

    allowed = {'card_id', 'player_id', 'name', 'overall', 'attack', 'defense', 'speed',
               'card_rarity', 'club', 'nation'}
    if sort not in allowed:
        sort = 'overall'

    if is_online():
        try:
            rdb      = _rdb()
            all_rows = rdb.table_rows('cards')
            clubs    = sorted({c['club']   for c in all_rows if c.get('club')})
            nations  = sorted({c['nation'] for c in all_rows if c.get('nation')})
            leagues  = sorted({c['league'] for c in all_rows if c.get('league')})
            rows = all_rows
            if search: rows = [c for c in rows if search.lower() in (c.get('name') or '').lower()]
            if rarity: rows = [c for c in rows if c.get('card_rarity') == rarity]
            if club:   rows = [c for c in rows if club.lower()   in (c.get('club')   or '').lower()]
            if nation: rows = [c for c in rows if nation.lower() in (c.get('nation') or '').lower()]
            if league: rows = [c for c in rows if league.lower() in (c.get('league') or '').lower()]
            num_sort = sort in ('overall', 'attack', 'defense', 'speed', 'card_id', 'player_id')
            reverse  = (order == 'desc')
            def _sort_val(c):
                v = c.get(sort)
                if v is None:
                    return (True, 0 if num_sort else '')
                if num_sort:
                    try:    return (False, int(v))
                    except: return (False, 0)
                return (False, str(v).lower())
            rows.sort(key=_sort_val, reverse=reverse)
        except Exception as e:
            flash(f'API error: {e}', 'error')
            rows, clubs, nations, leagues = [], [], [], []
        return render_template('cards.html',
            cards=rows, search=search, rarity=rarity, club=club,
            nation=nation, league=league, sort=sort, order=order,
            rarities=RARITIES, clubs=clubs, nations=nations, leagues=leagues,
            total=len(rows))

    # — Local mode —
    order_sql = 'DESC' if order == 'desc' else 'ASC'
    query  = 'SELECT * FROM cards WHERE 1=1'
    params = []
    if search: query += ' AND name LIKE ?';     params.append(f'%{search}%')
    if rarity: query += ' AND card_rarity = ?'; params.append(rarity)
    if club:   query += ' AND club LIKE ?';      params.append(f'%{club}%')
    if nation: query += ' AND nation LIKE ?';    params.append(f'%{nation}%')
    if league: query += ' AND league LIKE ?';    params.append(f'%{league}%')
    if sort == 'player_id':
        query += f' ORDER BY CAST(player_id AS INTEGER) {order_sql}'
    else:
        query += f' ORDER BY {sort} {order_sql}'
    conn    = get_db()
    rows    = conn.execute(query, params).fetchall()
    clubs   = [r[0] for r in conn.execute('SELECT DISTINCT club   FROM cards ORDER BY club').fetchall()]
    nations = [r[0] for r in conn.execute('SELECT DISTINCT nation FROM cards ORDER BY nation').fetchall()]
    leagues = [r[0] for r in conn.execute('SELECT DISTINCT league FROM cards ORDER BY league').fetchall()]
    conn.close()
    return render_template('cards.html',
        cards=rows, search=search, rarity=rarity, club=club,
        nation=nation, league=league, sort=sort, order=order,
        rarities=RARITIES, clubs=clubs, nations=nations, leagues=leagues,
        total=len(rows))


@app.route('/cards/add', methods=['GET', 'POST'])
def add_card():
    if is_online():
        try:
            rdb        = _rdb()
            all_rows   = rdb.table_rows('cards')
            card_types = sorted({c['card_type'] for c in all_rows if c.get('card_type')})
            for d in ('Standard', 'Icon', 'Hero', 'Normal', 'Euro TOTT', 'Copa America TOTT'):
                if d not in card_types:
                    card_types.append(d)
            if request.method == 'POST':
                fields = _parse_card_form(request.form)
                error  = _validate_card(fields)
                if error:
                    flash(error, 'error')
                    return render_template('add_card.html', card=fields,
                                           rarities=RARITIES, card_types=card_types,
                                           positions=POSITIONS)
                new_id = rdb.insert('cards', fields)
                flash(f'Card "{fields["name"]}" added (ID {new_id}).', 'success')
                return redirect(url_for('cards'))
            return render_template('add_card.html', card={}, rarities=RARITIES,
                                   card_types=card_types, positions=POSITIONS)
        except Exception as e:
            flash(f'API error: {e}', 'error')
            return redirect(url_for('cards'))

    # — Local mode —
    conn       = get_db()
    card_types = get_card_types(conn)
    if request.method == 'POST':
        fields = _parse_card_form(request.form)
        error  = _validate_card(fields)
        if error:
            conn.close()
            flash(error, 'error')
            return render_template('add_card.html', card=fields, rarities=RARITIES,
                                   card_types=card_types, positions=POSITIONS)
        conn.execute('''
            INSERT INTO cards (player_id, name, attack, defense, speed, height,
                               club, position, overall, image_path, card_rarity,
                               card_type, league, nation)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (fields['player_id'], fields['name'], fields['attack'], fields['defense'],
              fields['speed'], fields['height'], fields['club'], fields['position'],
              fields['overall'], fields['image_path'], fields['card_rarity'],
              fields['card_type'], fields['league'], fields['nation']))
        conn.commit()
        new_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        conn.close()
        flash(f'Card "{fields["name"]}" added (ID {new_id}).', 'success')
        return redirect(url_for('cards'))
    conn.close()
    return render_template('add_card.html', card={}, rarities=RARITIES,
                           card_types=card_types, positions=POSITIONS)


@app.route('/cards/<int:card_id>', methods=['GET', 'POST'])
def edit_card(card_id):
    if is_online():
        try:
            rdb      = _rdb()
            all_rows = rdb.table_rows('cards')
            card     = next((c for c in all_rows if c.get('card_id') == card_id), None)
            card_types = sorted({c['card_type'] for c in all_rows if c.get('card_type')})
            for d in ('Standard', 'Icon', 'Hero', 'Normal', 'Euro TOTT', 'Copa America TOTT'):
                if d not in card_types:
                    card_types.append(d)
            if card is None:
                flash('Card not found.', 'error')
                return redirect(url_for('cards'))
            if request.method == 'POST':
                fields = _parse_card_form(request.form)
                error  = _validate_card(fields)
                if error:
                    flash(error, 'error')
                    return render_template('edit_card.html', card={**card, **fields},
                                           rarities=RARITIES, card_types=card_types,
                                           positions=POSITIONS)
                rdb.update('cards', card_id, fields)  # card_id == rowid (INTEGER PRIMARY KEY)
                flash(f'Card "{fields["name"]}" updated.', 'success')
                return redirect(url_for('cards'))
            return render_template('edit_card.html', card=card, rarities=RARITIES,
                                   card_types=card_types, positions=POSITIONS)
        except Exception as e:
            flash(f'API error: {e}', 'error')
            return redirect(url_for('cards'))

    # — Local mode —
    conn       = get_db()
    card_types = get_card_types(conn)
    if request.method == 'POST':
        fields = _parse_card_form(request.form)
        error  = _validate_card(fields)
        if error:
            flash(error, 'error')
            card = conn.execute('SELECT * FROM cards WHERE card_id = ?', (card_id,)).fetchone()
            conn.close()
            return render_template('edit_card.html', card={**dict(card), **fields},
                                   rarities=RARITIES, card_types=card_types, positions=POSITIONS)
        conn.execute('''
            UPDATE cards SET player_id=?, name=?, attack=?, defense=?, speed=?,
                height=?, club=?, position=?, overall=?, image_path=?,
                card_rarity=?, card_type=?, league=?, nation=?
            WHERE card_id=?
        ''', (fields['player_id'], fields['name'], fields['attack'], fields['defense'],
              fields['speed'], fields['height'], fields['club'], fields['position'],
              fields['overall'], fields['image_path'], fields['card_rarity'],
              fields['card_type'], fields['league'], fields['nation'], card_id))
        conn.commit()
        conn.close()
        flash(f'Card "{fields["name"]}" updated.', 'success')
        return redirect(url_for('cards'))
    card = conn.execute('SELECT * FROM cards WHERE card_id = ?', (card_id,)).fetchone()
    conn.close()
    if card is None:
        flash('Card not found.', 'error')
        return redirect(url_for('cards'))
    return render_template('edit_card.html', card=dict(card), rarities=RARITIES,
                           card_types=card_types, positions=POSITIONS)


@app.route('/cards/<int:card_id>/delete', methods=['POST'])
def delete_card(card_id):
    if is_online():
        try:
            rdb  = _rdb()
            rows = rdb.table_rows('cards')
            card = next((c for c in rows if c.get('card_id') == card_id), None)
            if card:
                rdb.delete('cards', card_id)  # card_id == rowid
                flash(f'Card "{card.get("name", card_id)}" deleted.', 'success')
            else:
                flash('Card not found.', 'error')
        except Exception as e:
            flash(f'API error: {e}', 'error')
        return redirect(url_for('cards'))

    # — Local mode —
    conn = get_db()
    row  = conn.execute('SELECT name FROM cards WHERE card_id = ?', (card_id,)).fetchone()
    if row:
        conn.execute('DELETE FROM cards WHERE card_id = ?', (card_id,))
        conn.commit()
        flash(f'Card "{row["name"]}" deleted.', 'success')
    else:
        flash('Card not found.', 'error')
    conn.close()
    return redirect(url_for('cards'))


# ═══════════════════════════════════════════════════════════════════════════════
# PLAYERS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/players')
def players():
    search = request.args.get('search', '').strip()
    sort   = request.args.get('sort', 'user_id')
    order  = request.args.get('order', 'asc')

    allowed = {'user_id', 'name', 'coins', 'daily_streak', 'battles_won', 'cards_dropped'}
    if sort not in allowed:
        sort = 'user_id'

    if is_online():
        try:
            rdb  = _rdb()
            rows = rdb.table_rows('players')
            cols = rdb.table_columns('players')
            if search:
                rows = [p for p in rows if
                        search.lower() in (p.get('name') or '').lower() or
                        search in str(p.get('user_id', ''))]
            if sort in cols:
                reverse = (order == 'desc')
                rows.sort(key=lambda p: (p.get(sort) is None,
                                         p.get(sort) or ''), reverse=reverse)
        except Exception as e:
            flash(f'API error: {e}', 'error')
            rows, cols = [], []
        return render_template('players.html', players=rows, cols=cols,
                               search=search, sort=sort, order=order, total=len(rows))

    # — Local mode —
    order_sql = 'DESC' if order == 'desc' else 'ASC'
    conn = get_db()
    cols = [r['name'] for r in conn.execute('PRAGMA table_info(players)').fetchall()]
    query  = 'SELECT * FROM players WHERE 1=1'
    params = []
    if search:
        query += ' AND (name LIKE ? OR CAST(user_id AS TEXT) LIKE ?)'
        params += [f'%{search}%', f'%{search}%']
    if sort in cols:
        query += f' ORDER BY {sort} {order_sql}'
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return render_template('players.html', players=rows, cols=cols,
                           search=search, sort=sort, order=order, total=len(rows))


@app.route('/players/<int:user_id>', methods=['GET', 'POST'])
def edit_player(user_id):
    readonly = {'user_id'}

    if is_online():
        try:
            rdb      = _rdb()
            all_rows = rdb.table_rows('players')
            cols     = rdb.table_columns('players')
            player   = next((p for p in all_rows if p.get('user_id') == user_id), None)
            if player is None:
                flash('Player not found.', 'error')
                return redirect(url_for('players'))
            if request.method == 'POST':
                updates = {}
                for col in cols:
                    if col in readonly:
                        continue
                    val = request.form.get(col, '').strip()
                    updates[col] = val if val != '' else None
                rdb.update('players', user_id, updates)  # user_id == rowid
                flash('Player updated.', 'success')
                return redirect(url_for('players'))
            return render_template('edit_player.html', player=player,
                                   cols=cols, readonly=readonly)
        except Exception as e:
            flash(f'API error: {e}', 'error')
            return redirect(url_for('players'))

    # — Local mode —
    conn = get_db()
    cols = [r['name'] for r in conn.execute('PRAGMA table_info(players)').fetchall()]
    if request.method == 'POST':
        updates = {}
        for col in cols:
            if col in readonly:
                continue
            val = request.form.get(col, '').strip()
            updates[col] = val if val != '' else None
        set_clause = ', '.join(f'{c} = ?' for c in updates)
        conn.execute(f'UPDATE players SET {set_clause} WHERE user_id = ?',
                     list(updates.values()) + [user_id])
        conn.commit()
        conn.close()
        flash('Player updated.', 'success')
        return redirect(url_for('players'))
    player = conn.execute('SELECT * FROM players WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    if player is None:
        flash('Player not found.', 'error')
        return redirect(url_for('players'))
    return render_template('edit_player.html', player=dict(player),
                           cols=cols, readonly={'user_id'})


# ═══════════════════════════════════════════════════════════════════════════════
# INVENTORIES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/inventories')
def inventories():
    user_filter = request.args.get('user_id', '').strip()
    card_filter = request.args.get('card_search', '').strip()

    if is_online():
        try:
            rdb       = _rdb()
            inv_rows  = rdb.table_rows('inventories')
            card_rows = rdb.table_rows('cards')
            plyr_rows = rdb.table_rows('players')
            card_map  = {c['card_id']: c for c in card_rows}
            plyr_map  = {p['user_id']: p for p in plyr_rows}
            enriched  = []
            for row in inv_rows:
                r = dict(row)
                card = card_map.get(r.get('card_id'), {})
                plyr = plyr_map.get(r.get('user_id'), {})
                r['card_name']   = card.get('name', '')
                r['overall']     = card.get('overall', '')
                r['card_rarity'] = card.get('card_rarity', '')
                r['player_name'] = plyr.get('name', '')
                enriched.append(r)
            if user_filter:
                enriched = [r for r in enriched if
                            user_filter.lower() in (r.get('player_name') or '').lower() or
                            user_filter in str(r.get('user_id', ''))]
            if card_filter:
                enriched = [r for r in enriched if
                            card_filter.lower() in (r.get('card_name') or '').lower()]
            enriched.sort(key=lambda r: ((r.get('player_name') or '').lower(),
                                         (r.get('card_name') or '').lower()))
            all_cards   = [{'card_id': c['card_id'], 'name': c['name'],
                            'overall': c['overall']} for c in card_rows]
            all_players = [{'user_id': p['user_id'], 'name': p['name']} for p in plyr_rows]
        except Exception as e:
            flash(f'API error: {e}', 'error')
            enriched, all_cards, all_players = [], [], []
        return render_template('inventories.html', rows=enriched, total=len(enriched),
                               user_filter=user_filter, card_filter=card_filter,
                               all_cards=all_cards, all_players=all_players)

    # — Local mode —
    query = '''
        SELECT i.rowid, i.user_id, p.name AS player_name,
               i.card_id, c.name AS card_name, c.overall, c.card_rarity, i.edition
        FROM inventories i
        LEFT JOIN cards   c ON i.card_id = c.card_id
        LEFT JOIN players p ON i.user_id = p.user_id
        WHERE 1=1
    '''
    params = []
    if user_filter:
        query += ' AND (CAST(i.user_id AS TEXT) LIKE ? OR p.name LIKE ?)'
        params += [f'%{user_filter}%', f'%{user_filter}%']
    if card_filter:
        query += ' AND c.name LIKE ?'
        params.append(f'%{card_filter}%')
    query += ' ORDER BY p.name, c.name'
    conn        = get_db()
    rows        = conn.execute(query, params).fetchall()
    all_cards   = conn.execute('SELECT card_id, name, overall FROM cards ORDER BY name').fetchall()
    all_players = conn.execute('SELECT user_id, name FROM players ORDER BY name').fetchall()
    conn.close()
    return render_template('inventories.html', rows=rows, total=len(rows),
                           user_filter=user_filter, card_filter=card_filter,
                           all_cards=all_cards, all_players=all_players)


@app.route('/inventories/add', methods=['POST'])
def add_inventory():
    user_id = request.form.get('user_id', '').strip()
    card_id = request.form.get('card_id', '').strip()
    if not user_id or not card_id:
        flash('User ID and Card ID are required.', 'error')
        return redirect(url_for('inventories'))

    if is_online():
        try:
            rdb       = _rdb()
            card_rows = rdb.table_rows('cards')
            card      = next((c for c in card_rows if c['card_id'] == int(card_id)), None)
            copies    = int(card['copies'] or 0) if card else 0
            edition   = copies + 1
            if card:
                rdb.update('cards', int(card_id), {'copies': copies + 1})
            rdb.insert('inventories', {
                'user_id': int(user_id), 'card_id': int(card_id), 'edition': edition
            })
            flash(f'Added card {card_id} to player {user_id} (edition #{edition}).', 'success')
        except Exception as e:
            flash(f'API error: {e}', 'error')
        return redirect(url_for('inventories'))

    # — Local mode —
    conn     = get_db()
    card_row = conn.execute('SELECT name, copies FROM cards WHERE card_id = ?', (int(card_id),)).fetchone()
    copies   = card_row['copies'] if card_row else 0
    edition  = copies + 1
    conn.execute('UPDATE cards SET copies = copies + 1 WHERE card_id = ?', (int(card_id),))
    conn.execute('INSERT INTO inventories (user_id, card_id, edition) VALUES (?, ?, ?)',
                 (int(user_id), int(card_id), edition))
    conn.commit()
    conn.close()
    flash(f'Added "{card_row["name"] if card_row else card_id}" to player {user_id} (edition #{edition}).', 'success')
    return redirect(url_for('inventories'))


@app.route('/inventories/<int:rowid>/delete', methods=['POST'])
def delete_inventory(rowid):
    if is_online():
        try:
            _rdb().delete('inventories', rowid)
            flash('Inventory entry removed.', 'success')
        except Exception as e:
            flash(f'API error: {e}', 'error')
        return redirect(url_for('inventories'))

    conn = get_db()
    conn.execute('DELETE FROM inventories WHERE rowid = ?', (rowid,))
    conn.commit()
    conn.close()
    flash('Inventory entry removed.', 'success')
    return redirect(url_for('inventories'))


# ═══════════════════════════════════════════════════════════════════════════════
# DECKS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/decks')
def decks():
    user_filter = request.args.get('user_id', '').strip()

    if is_online():
        try:
            rdb       = _rdb()
            deck_rows = rdb.table_rows('decks')
            plyr_rows = rdb.table_rows('players')
            plyr_map  = {p['user_id']: p for p in plyr_rows}
            enriched  = []
            for row in deck_rows:
                r = dict(row)
                r['player_name'] = plyr_map.get(r.get('user_id'), {}).get('name', '')
                r['card_count']  = len(r['cards'].split(',')) if r.get('cards') else 0
                enriched.append(r)
            if user_filter:
                enriched = [r for r in enriched if
                            user_filter.lower() in (r.get('player_name') or '').lower() or
                            user_filter in str(r.get('user_id', ''))]
            enriched.sort(key=lambda r: ((r.get('player_name') or '').lower(),
                                         (r.get('deck_name') or '').lower()))
        except Exception as e:
            flash(f'API error: {e}', 'error')
            enriched = []
        return render_template('decks.html', decks=enriched, user_filter=user_filter,
                               total=len(enriched))

    # — Local mode —
    query = '''
        SELECT d.rowid, d.user_id, p.name AS player_name,
               d.deck_name, d.cards
        FROM decks d
        LEFT JOIN players p ON d.user_id = p.user_id
        WHERE 1=1
    '''
    params = []
    if user_filter:
        query += ' AND (CAST(d.user_id AS TEXT) LIKE ? OR p.name LIKE ?)'
        params += [f'%{user_filter}%', f'%{user_filter}%']
    query += ' ORDER BY p.name, d.deck_name'
    conn = get_db()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    annotated = []
    for row in rows:
        d = dict(row)
        d['card_count'] = len(d['cards'].split(',')) if d['cards'] else 0
        annotated.append(d)
    return render_template('decks.html', decks=annotated, user_filter=user_filter,
                           total=len(annotated))


@app.route('/decks/<int:rowid>/delete', methods=['POST'])
def delete_deck(rowid):
    if is_online():
        try:
            _rdb().delete('decks', rowid)
            flash('Deck deleted.', 'success')
        except Exception as e:
            flash(f'API error: {e}', 'error')
        return redirect(url_for('decks'))

    conn = get_db()
    row = conn.execute('SELECT deck_name FROM decks WHERE rowid = ?', (rowid,)).fetchone()
    if row:
        conn.execute('DELETE FROM decks WHERE rowid = ?', (rowid,))
        conn.commit()
        flash(f'Deck "{row["deck_name"]}" deleted.', 'success')
    conn.close()
    return redirect(url_for('decks'))


# ═══════════════════════════════════════════════════════════════════════════════
# WISHLISTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/wishlists')
def wishlists():
    if is_online():
        try:
            rdb = _rdb()
            if not rdb.table_exists('wishlists'):
                return render_template('wishlists.html', rows=[], user_filter='',
                                       total=0, table_missing=True)
            user_filter = request.args.get('user_id', '').strip()
            wl_rows    = rdb.table_rows('wishlists')
            card_rows  = rdb.table_rows('cards')
            plyr_rows  = rdb.table_rows('players')
            card_map   = {c['card_id']: c for c in card_rows}
            plyr_map   = {p['user_id']: p for p in plyr_rows}
            enriched   = []
            for row in wl_rows:
                r = dict(row)
                card = card_map.get(r.get('card_id'), {})
                plyr = plyr_map.get(r.get('user_id'), {})
                r['card_name']   = card.get('name', '')
                r['overall']     = card.get('overall', '')
                r['card_rarity'] = card.get('card_rarity', '')
                r['player_name'] = plyr.get('name', '')
                enriched.append(r)
            if user_filter:
                enriched = [r for r in enriched if
                            user_filter.lower() in (r.get('player_name') or '').lower() or
                            user_filter in str(r.get('user_id', ''))]
            enriched.sort(key=lambda r: ((r.get('player_name') or '').lower(),
                                         (r.get('card_name') or '').lower()))
        except Exception as e:
            flash(f'API error: {e}', 'error')
            enriched, user_filter = [], ''
        return render_template('wishlists.html', rows=enriched,
                               user_filter=user_filter, total=len(enriched), table_missing=False)

    # — Local mode —
    conn = get_db()
    if not table_exists(conn, 'wishlists'):
        conn.close()
        return render_template('wishlists.html', rows=[], user_filter='',
                               total=0, table_missing=True)
    user_filter = request.args.get('user_id', '').strip()
    query = '''
        SELECT w.rowid, w.user_id, p.name AS player_name,
               w.card_id, c.name AS card_name, c.overall, c.card_rarity
        FROM wishlists w
        LEFT JOIN cards   c ON w.card_id = c.card_id
        LEFT JOIN players p ON w.user_id = p.user_id
        WHERE 1=1
    '''
    params = []
    if user_filter:
        query += ' AND (CAST(w.user_id AS TEXT) LIKE ? OR p.name LIKE ?)'
        params += [f'%{user_filter}%', f'%{user_filter}%']
    query += ' ORDER BY p.name, c.name'
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return render_template('wishlists.html', rows=rows, user_filter=user_filter,
                           total=len(rows), table_missing=False)


@app.route('/wishlists/<int:rowid>/delete', methods=['POST'])
def delete_wishlist(rowid):
    if is_online():
        try:
            _rdb().delete('wishlists', rowid)
            flash('Wishlist entry removed.', 'success')
        except Exception as e:
            flash(f'API error: {e}', 'error')
        return redirect(url_for('wishlists'))

    conn = get_db()
    conn.execute('DELETE FROM wishlists WHERE rowid = ?', (rowid,))
    conn.commit()
    conn.close()
    flash('Wishlist entry removed.', 'success')
    return redirect(url_for('wishlists'))


# ═══════════════════════════════════════════════════════════════════════════════
# PACKS  (table: packs — one row per player, one column per pack type)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/packs')
def packs():
    if is_online():
        try:
            rdb = _rdb()
            if not rdb.table_exists('packs'):
                return render_template('packs.html', rows=[], user_filter='', total=0,
                                       pack_cols=[], all_players=[], table_missing=True)
            user_filter = request.args.get('user_id', '').strip()
            pk_rows    = rdb.table_rows('packs')
            plyr_rows  = rdb.table_rows('players')
            plyr_map   = {p['user_id']: p for p in plyr_rows}
            pack_cols  = [k for k in (list(pk_rows[0].keys()) if pk_rows else [])
                          if k not in ('user_id', 'rowid')]
            enriched   = []
            for row in pk_rows:
                r = dict(row)
                r['player_name'] = plyr_map.get(r.get('user_id'), {}).get('name', '')
                enriched.append(r)
            if user_filter:
                enriched = [r for r in enriched if
                            user_filter.lower() in (r.get('player_name') or '').lower() or
                            user_filter in str(r.get('user_id', ''))]
            enriched.sort(key=lambda r: (r.get('player_name') or '').lower())
            all_players = [{'user_id': p['user_id'], 'name': p['name']} for p in plyr_rows]
        except Exception as e:
            flash(f'API error: {e}', 'error')
            enriched, pack_cols, user_filter, all_players = [], [], '', []
        return render_template('packs.html', rows=enriched, user_filter=user_filter,
                               total=len(enriched), pack_cols=pack_cols,
                               all_players=all_players, table_missing=False)

    # — Local mode —
    conn = get_db()
    if not table_exists(conn, 'packs'):
        conn.close()
        return render_template('packs.html', rows=[], user_filter='', total=0,
                               pack_cols=[], all_players=[], table_missing=True)
    user_filter = request.args.get('user_id', '').strip()
    pack_cols   = get_pack_cols(conn)
    query = '''
        SELECT pk.*, p.name AS player_name
        FROM packs pk
        LEFT JOIN players p ON pk.user_id = p.user_id
        WHERE 1=1
    '''
    params = []
    if user_filter:
        query += ' AND (CAST(pk.user_id AS TEXT) LIKE ? OR p.name LIKE ?)'
        params += [f'%{user_filter}%', f'%{user_filter}%']
    query += ' ORDER BY p.name'
    rows        = conn.execute(query, params).fetchall()
    all_players = conn.execute('SELECT user_id, name FROM players ORDER BY name').fetchall()
    conn.close()
    return render_template('packs.html', rows=rows, user_filter=user_filter,
                           total=len(rows), pack_cols=pack_cols,
                           all_players=all_players, table_missing=False)


@app.route('/packs/<int:user_id>/<col>', methods=['POST'])
def edit_pack(user_id, col):
    qty = request.form.get('quantity', '').strip()
    if not qty.lstrip('-').isdigit():
        flash('Quantity must be a number.', 'error')
        return redirect(url_for('packs'))

    if is_online():
        try:
            rdb      = _rdb()
            pk_rows  = rdb.table_rows('packs')
            pack_cols = [k for k in (list(pk_rows[0].keys()) if pk_rows else [])
                         if k not in ('user_id', 'rowid')]
            if col not in pack_cols:
                flash('Invalid pack column.', 'error')
                return redirect(url_for('packs'))
            row = next((r for r in pk_rows if r.get('user_id') == user_id), None)
            if row is None:
                flash('Pack record not found.', 'error')
                return redirect(url_for('packs'))
            rdb.update('packs', row['rowid'], {col: int(qty)})
            flash('Pack quantity updated.', 'success')
        except Exception as e:
            flash(f'API error: {e}', 'error')
        return redirect(url_for('packs'))

    # — Local mode —
    conn = get_db()
    valid_cols = get_pack_cols(conn)
    if col not in valid_cols:
        conn.close()
        flash('Invalid pack column.', 'error')
        return redirect(url_for('packs'))
    conn.execute(f'UPDATE packs SET {col} = ? WHERE user_id = ?', (int(qty), user_id))
    conn.commit()
    conn.close()
    flash('Pack quantity updated.', 'success')
    return redirect(url_for('packs'))


@app.route('/packs/<int:rowid>/delete', methods=['POST'])
def delete_pack(rowid):
    if is_online():
        try:
            _rdb().delete('packs', rowid)
            flash('Pack entry removed.', 'success')
        except Exception as e:
            flash(f'API error: {e}', 'error')
        return redirect(url_for('packs'))

    conn = get_db()
    conn.execute('DELETE FROM packs WHERE rowid = ?', (rowid,))
    conn.commit()
    conn.close()
    flash('Pack entry removed.', 'success')
    return redirect(url_for('packs'))


@app.route('/packs/give', methods=['POST'])
def give_pack():
    user_id  = request.form.get('user_id', '').strip()
    pack_col = request.form.get('pack_col', '').strip()
    qty      = request.form.get('quantity', '1').strip()

    if not user_id or not pack_col:
        flash('Player and pack type are required.', 'error')
        return redirect(url_for('packs'))
    if not qty.isdigit() or int(qty) <= 0:
        flash('Quantity must be a positive whole number.', 'error')
        return redirect(url_for('packs'))

    qty     = int(qty)
    user_id = int(user_id)

    if is_online():
        try:
            rdb       = _rdb()
            pk_rows   = rdb.table_rows('packs')
            pack_cols = [k for k in (list(pk_rows[0].keys()) if pk_rows else [])
                         if k not in ('user_id', 'rowid')]
            if pack_col not in pack_cols:
                flash('Invalid pack type.', 'error')
                return redirect(url_for('packs'))
            row = next((r for r in pk_rows if r.get('user_id') == user_id), None)
            if row:
                current = int(row.get(pack_col) or 0)
                rdb.update('packs', row['rowid'], {pack_col: current + qty})
            else:
                new_data = {'user_id': user_id}
                for col in pack_cols:
                    new_data[col] = qty if col == pack_col else 0
                rdb.insert('packs', new_data)
            flash(f'Gave {qty}× {pack_col.replace("_", " ").title()} to player {user_id}.', 'success')
        except Exception as e:
            flash(f'API error: {e}', 'error')
        return redirect(url_for('packs'))

    # — Local mode —
    conn       = get_db()
    valid_cols = get_pack_cols(conn)
    if pack_col not in valid_cols:
        conn.close()
        flash('Invalid pack type.', 'error')
        return redirect(url_for('packs'))
    existing = conn.execute('SELECT 1 FROM packs WHERE user_id = ?', (user_id,)).fetchone()
    if existing:
        conn.execute(f'UPDATE packs SET {pack_col} = {pack_col} + ? WHERE user_id = ?', (qty, user_id))
    else:
        cols_str = ', '.join(valid_cols)
        zeros    = ', '.join('0' for _ in valid_cols)
        conn.execute(f'INSERT INTO packs (user_id, {cols_str}) VALUES (?, {zeros})', (user_id,))
        conn.execute(f'UPDATE packs SET {pack_col} = {pack_col} + ? WHERE user_id = ?', (qty, user_id))
    conn.commit()
    player = conn.execute('SELECT name FROM players WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    name = player['name'] if player else str(user_id)
    flash(f'Gave {qty}× {pack_col.replace("_", " ").title()} to {name}.', 'success')
    return redirect(url_for('packs'))


# ═══════════════════════════════════════════════════════════════════════════════
# ACHIEVEMENTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/achievements')
def achievements():
    if is_online():
        try:
            rdb = _rdb()
            if not rdb.table_exists('achievements'):
                return render_template('achievements.html', rows=[], total=0, table_missing=True)
            rows = rdb.table_rows('achievements')
            rows.sort(key=lambda a: a.get('achievement_id') or 0)
        except Exception as e:
            flash(f'API error: {e}', 'error')
            rows = []
        return render_template('achievements.html', rows=rows, total=len(rows), table_missing=False)

    # — Local mode —
    conn = get_db()
    if not table_exists(conn, 'achievements'):
        conn.close()
        return render_template('achievements.html', rows=[], total=0, table_missing=True)
    rows = conn.execute('SELECT * FROM achievements ORDER BY achievement_id').fetchall()
    conn.close()
    return render_template('achievements.html', rows=rows, total=len(rows), table_missing=False)


@app.route('/achievements/add', methods=['GET', 'POST'])
def add_achievement():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        desc  = request.form.get('description', '').strip()
        if not title:
            flash('Title is required.', 'error')
            return render_template('edit_achievement.html', ach={}, is_new=True)
        if is_online():
            try:
                _rdb().insert('achievements', {'title': title, 'description': desc})
                flash(f'Achievement "{title}" added.', 'success')
            except Exception as e:
                flash(f'API error: {e}', 'error')
            return redirect(url_for('achievements'))
        conn = get_db()
        conn.execute('INSERT INTO achievements (title, description) VALUES (?, ?)', (title, desc))
        conn.commit()
        conn.close()
        flash(f'Achievement "{title}" added.', 'success')
        return redirect(url_for('achievements'))
    return render_template('edit_achievement.html', ach={}, is_new=True)


@app.route('/achievements/<int:achievement_id>', methods=['GET', 'POST'])
def edit_achievement(achievement_id):
    if is_online():
        try:
            rdb  = _rdb()
            rows = rdb.table_rows('achievements')
            ach  = next((a for a in rows if a.get('achievement_id') == achievement_id), None)
            if ach is None:
                flash('Achievement not found.', 'error')
                return redirect(url_for('achievements'))
            if request.method == 'POST':
                title = request.form.get('title', '').strip()
                desc  = request.form.get('description', '').strip()
                if not title:
                    flash('Title is required.', 'error')
                    return render_template('edit_achievement.html', ach=ach, is_new=False)
                rdb.update('achievements', achievement_id, {'title': title, 'description': desc})
                flash('Achievement updated.', 'success')
                return redirect(url_for('achievements'))
            return render_template('edit_achievement.html', ach=ach, is_new=False)
        except Exception as e:
            flash(f'API error: {e}', 'error')
            return redirect(url_for('achievements'))

    # — Local mode —
    conn = get_db()
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        desc  = request.form.get('description', '').strip()
        if not title:
            flash('Title is required.', 'error')
            ach = conn.execute('SELECT * FROM achievements WHERE achievement_id = ?',
                               (achievement_id,)).fetchone()
            conn.close()
            return render_template('edit_achievement.html', ach=dict(ach), is_new=False)
        conn.execute('UPDATE achievements SET title = ?, description = ? WHERE achievement_id = ?',
                     (title, desc, achievement_id))
        conn.commit()
        conn.close()
        flash('Achievement updated.', 'success')
        return redirect(url_for('achievements'))
    ach = conn.execute('SELECT * FROM achievements WHERE achievement_id = ?',
                       (achievement_id,)).fetchone()
    conn.close()
    if ach is None:
        flash('Achievement not found.', 'error')
        return redirect(url_for('achievements'))
    return render_template('edit_achievement.html', ach=dict(ach), is_new=False)


@app.route('/achievements/<int:achievement_id>/delete', methods=['POST'])
def delete_achievement(achievement_id):
    if is_online():
        try:
            rdb  = _rdb()
            rows = rdb.table_rows('achievements')
            ach  = next((a for a in rows if a.get('achievement_id') == achievement_id), None)
            if ach:
                rdb.delete('achievements', achievement_id)
                flash(f'Achievement "{ach.get("title", achievement_id)}" deleted.', 'success')
            else:
                flash('Achievement not found.', 'error')
        except Exception as e:
            flash(f'API error: {e}', 'error')
        return redirect(url_for('achievements'))

    # — Local mode —
    conn = get_db()
    row = conn.execute('SELECT title FROM achievements WHERE achievement_id = ?',
                       (achievement_id,)).fetchone()
    if row:
        conn.execute('DELETE FROM achievements WHERE achievement_id = ?', (achievement_id,))
        conn.commit()
        flash(f'Achievement "{row["title"]}" deleted.', 'success')
    conn.close()
    return redirect(url_for('achievements'))


# ═══════════════════════════════════════════════════════════════════════════════
# USER ACHIEVEMENTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/user_achievements')
def user_achievements():
    if is_online():
        try:
            rdb = _rdb()
            if not rdb.table_exists('user_achievements'):
                return render_template('user_achievements.html', rows=[], user_filter='',
                                       total=0, table_missing=True)
            user_filter = request.args.get('user_id', '').strip()
            ua_rows    = rdb.table_rows('user_achievements')
            plyr_rows  = rdb.table_rows('players')
            ach_rows   = rdb.table_rows('achievements')
            plyr_map   = {p['user_id']: p for p in plyr_rows}
            ach_map    = {a['achievement_id']: a for a in ach_rows}
            enriched   = []
            for row in ua_rows:
                r = dict(row)
                plyr = plyr_map.get(r.get('user_id'), {})
                ach  = ach_map.get(r.get('achievement_id'), {})
                r['player_name'] = plyr.get('name', '')
                r['ach_title']   = ach.get('title', '')
                enriched.append(r)
            if user_filter:
                enriched = [r for r in enriched if
                            user_filter.lower() in (r.get('player_name') or '').lower() or
                            user_filter in str(r.get('user_id', ''))]
            enriched.sort(key=lambda r: ((r.get('player_name') or '').lower(),
                                         r.get('date_earned') or ''), reverse=True)
        except Exception as e:
            flash(f'API error: {e}', 'error')
            enriched, user_filter = [], ''
        return render_template('user_achievements.html', rows=enriched,
                               user_filter=user_filter, total=len(enriched), table_missing=False)

    # — Local mode —
    conn = get_db()
    if not table_exists(conn, 'user_achievements'):
        conn.close()
        return render_template('user_achievements.html', rows=[], user_filter='',
                               total=0, table_missing=True)
    user_filter = request.args.get('user_id', '').strip()
    query = '''
        SELECT ua.rowid, ua.user_id, p.name AS player_name,
               ua.achievement_id, a.title AS ach_title, ua.date_earned
        FROM user_achievements ua
        LEFT JOIN players      p ON ua.user_id        = p.user_id
        LEFT JOIN achievements a ON ua.achievement_id = a.achievement_id
        WHERE 1=1
    '''
    params = []
    if user_filter:
        query += ' AND (CAST(ua.user_id AS TEXT) LIKE ? OR p.name LIKE ?)'
        params += [f'%{user_filter}%', f'%{user_filter}%']
    query += ' ORDER BY p.name, ua.date_earned DESC'
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return render_template('user_achievements.html', rows=rows, user_filter=user_filter,
                           total=len(rows), table_missing=False)


@app.route('/user_achievements/<int:rowid>/delete', methods=['POST'])
def delete_user_achievement(rowid):
    if is_online():
        try:
            _rdb().delete('user_achievements', rowid)
            flash('User achievement removed.', 'success')
        except Exception as e:
            flash(f'API error: {e}', 'error')
        return redirect(url_for('user_achievements'))

    conn = get_db()
    conn.execute('DELETE FROM user_achievements WHERE rowid = ?', (rowid,))
    conn.commit()
    conn.close()
    flash('User achievement removed.', 'success')
    return redirect(url_for('user_achievements'))


# ═══════════════════════════════════════════════════════════════════════════════
# TRANSFER MARKET
# ═══════════════════════════════════════════════════════════════════════════════

def _market_time_left(expires_at_str):
    try:
        from datetime import datetime as _dt, timezone as _tz
        expires = _dt.strptime(expires_at_str, '%Y-%m-%d %H:%M:%S')
        delta = expires - _dt.now(_tz.utc).replace(tzinfo=None)
        if delta.total_seconds() <= 0:
            return 'Expired'
        h, rem = divmod(int(delta.total_seconds()), 3600)
        m = rem // 60
        return f"{h}h {m}m"
    except:
        return '?'


@app.route('/market_listings')
def market_listings():
    search      = request.args.get('search', '').strip()
    status_filter = request.args.get('status', 'active')   # active / sold / cancelled / expired / all

    if is_online():
        try:
            rdb = _rdb()
            if not rdb.table_exists('market_listings'):
                return render_template('market_listings.html', rows=[], search=search,
                                       status_filter=status_filter, total=0, table_missing=True)
            ml_rows   = rdb.table_rows('market_listings')
            card_rows = rdb.table_rows('cards')
            plyr_rows = rdb.table_rows('players')
            card_map  = {c['card_id']: c for c in card_rows}
            plyr_map  = {p['user_id']: p for p in plyr_rows}
            enriched = []
            for row in ml_rows:
                r = dict(row)
                card  = card_map.get(r.get('card_id'), {})
                plyr  = plyr_map.get(r.get('seller_id'), {})
                buyer = plyr_map.get(r.get('buyer_id'), {})
                r['card_name']   = card.get('name', '')
                r['overall']     = card.get('overall', '')
                r['card_rarity'] = card.get('card_rarity', '')
                r['seller_name'] = plyr.get('name', '')
                r['buyer_name']  = buyer.get('name', '') if r.get('buyer_id') else ''
                r['time_left']   = _market_time_left(r.get('expires_at', '')) if r.get('status') == 'active' else '—'
                enriched.append(r)
            if status_filter != 'all':
                enriched = [r for r in enriched if r.get('status') == status_filter]
            if search:
                enriched = [r for r in enriched if
                            search.lower() in (r.get('card_name') or '').lower() or
                            search.lower() in (r.get('seller_name') or '').lower()]
            enriched.sort(key=lambda r: r.get('listed_at') or '', reverse=True)
        except Exception as e:
            flash(f'API error: {e}', 'error')
            enriched = []
        return render_template('market_listings.html', rows=enriched, search=search,
                               status_filter=status_filter, total=len(enriched), table_missing=False)

    # — Local mode —
    conn = get_db()
    if not table_exists(conn, 'market_listings'):
        conn.close()
        return render_template('market_listings.html', rows=[], search=search,
                               status_filter=status_filter, total=0, table_missing=True)

    query = '''
        SELECT ml.listing_id, ml.seller_id, p.name AS seller_name,
               ml.card_id, c.name AS card_name, c.overall, c.card_rarity,
               ml.edition, ml.price, ml.listed_at, ml.expires_at, ml.trade_count,
               ml.status, ml.resolved_at, ml.buyer_id,
               bp.name AS buyer_name
        FROM market_listings ml
        JOIN cards   c  ON ml.card_id   = c.card_id
        JOIN players p  ON ml.seller_id = p.user_id
        LEFT JOIN players bp ON ml.buyer_id = bp.user_id
        WHERE 1=1
    '''
    params = []
    if status_filter != 'all':
        query += ' AND ml.status = ?'
        params.append(status_filter)
    if search:
        query += ' AND (c.name LIKE ? OR p.name LIKE ?)'
        params += [f'%{search}%', f'%{search}%']
    query += ' ORDER BY ml.listed_at DESC'

    rows = conn.execute(query, params).fetchall()
    conn.close()

    enriched = []
    for row in rows:
        r = dict(row)
        r['time_left'] = _market_time_left(r.get('expires_at', '')) if r.get('status') == 'active' else '—'
        enriched.append(r)

    return render_template('market_listings.html', rows=enriched, search=search,
                           status_filter=status_filter, total=len(enriched), table_missing=False)


@app.route('/market_listings/<int:listing_id>/delete', methods=['POST'])
def delete_market_listing(listing_id):
    if is_online():
        try:
            _rdb().delete('market_listings', listing_id)
            flash('Listing removed.', 'success')
        except Exception as e:
            flash(f'API error: {e}', 'error')
        return redirect(url_for('market_listings'))

    # — Local mode — hard delete (admin action)
    conn = get_db()
    row = conn.execute(
        'SELECT seller_id, card_id, edition, trade_count, status FROM market_listings WHERE listing_id = ?', (listing_id,)
    ).fetchone()
    if row:
        if row['status'] == 'active':
            conn.execute(
                'INSERT INTO inventories (user_id, card_id, edition, trade_count) VALUES (?, ?, ?, ?)',
                (row['seller_id'], row['card_id'], row['edition'], row['trade_count'])
            )
            flash('Listing removed — card returned to seller\'s inventory.', 'success')
        else:
            flash('Listing record deleted.', 'success')
        conn.execute('DELETE FROM market_listings WHERE listing_id = ?', (listing_id,))
        conn.commit()
    else:
        flash('Listing not found.', 'error')
    conn.close()
    return redirect(url_for('market_listings'))


# ═══════════════════════════════════════════════════════════════════════════════
# FRIENDS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/friends')
def friends():
    mode   = request.args.get('mode', 'friendships')   # friendships | requests
    search = request.args.get('search', '').strip()

    if is_online():
        try:
            rdb = _rdb()
            tables = rdb.tables()
            missing_friends  = 'friendships'     not in tables
            missing_requests = 'friend_requests' not in tables

            plyr_rows = rdb.table_rows('players') if not (missing_friends and missing_requests) else []
            plyr_map  = {p['user_id']: p for p in plyr_rows}

            friendships = []
            requests_rows = []

            if not missing_friends:
                raw = rdb.table_rows('friendships')
                seen = set()
                for r in raw:
                    a, b = r.get('user_id'), r.get('friend_id')
                    if a is None or b is None:
                        continue
                    pair = tuple(sorted([a, b]))
                    if pair in seen:
                        continue
                    seen.add(pair)
                    friendships.append({
                        'user_id':     pair[0],
                        'user_name':   plyr_map.get(pair[0], {}).get('name', ''),
                        'friend_id':   pair[1],
                        'friend_name': plyr_map.get(pair[1], {}).get('name', ''),
                        'added_at':    r.get('added_at', ''),
                    })

            if not missing_requests:
                for r in rdb.table_rows('friend_requests'):
                    requests_rows.append({
                        'request_id':     r.get('request_id'),
                        'requester_id':   r.get('requester_id'),
                        'requester_name': plyr_map.get(r.get('requester_id'), {}).get('name', ''),
                        'recipient_id':   r.get('recipient_id'),
                        'recipient_name': plyr_map.get(r.get('recipient_id'), {}).get('name', ''),
                        'requested_at':   r.get('requested_at', ''),
                        'notified':       r.get('notified', 0),
                    })

            if search:
                s = search.lower()
                friendships = [r for r in friendships if
                               s in (r['user_name'] or '').lower() or
                               s in (r['friend_name'] or '').lower() or
                               s in str(r['user_id']) or s in str(r['friend_id'])]
                requests_rows = [r for r in requests_rows if
                                 s in (r['requester_name'] or '').lower() or
                                 s in (r['recipient_name'] or '').lower() or
                                 s in str(r['requester_id']) or s in str(r['recipient_id'])]

            friendships.sort(key=lambda r: ((r['user_name'] or '').lower(), (r['friend_name'] or '').lower()))
            requests_rows.sort(key=lambda r: r.get('requested_at') or '', reverse=True)
        except Exception as e:
            flash(f'API error: {e}', 'error')
            friendships, requests_rows = [], []
            missing_friends = missing_requests = False

        if missing_friends and missing_requests:
            return render_template('friends.html', mode=mode, search=search,
                                   friendships=[], requests=[], total=0, table_missing=True)
        rows = friendships if mode == 'friendships' else requests_rows
        return render_template('friends.html', mode=mode, search=search,
                               friendships=friendships, requests=requests_rows,
                               total=len(rows), table_missing=False)

    # — Local mode —
    conn = get_db()
    missing_friends  = not table_exists(conn, 'friendships')
    missing_requests = not table_exists(conn, 'friend_requests')
    if missing_friends and missing_requests:
        conn.close()
        return render_template('friends.html', mode=mode, search=search,
                               friendships=[], requests=[], total=0, table_missing=True)

    friendships = []
    if not missing_friends:
        query = '''
            SELECT f.user_id, p1.name AS user_name, f.friend_id, p2.name AS friend_name, f.added_at
            FROM friendships f
            LEFT JOIN players p1 ON f.user_id   = p1.user_id
            LEFT JOIN players p2 ON f.friend_id = p2.user_id
            WHERE f.user_id < f.friend_id
        '''
        params = []
        if search:
            query += ' AND (p1.name LIKE ? OR p2.name LIKE ? OR CAST(f.user_id AS TEXT) LIKE ? OR CAST(f.friend_id AS TEXT) LIKE ?)'
            s = f'%{search}%'
            params += [s, s, s, s]
        query += ' ORDER BY p1.name, p2.name'
        friendships = [dict(r) for r in conn.execute(query, params).fetchall()]

    requests_rows = []
    if not missing_requests:
        query = '''
            SELECT fr.request_id, fr.requester_id, p1.name AS requester_name,
                   fr.recipient_id, p2.name AS recipient_name, fr.requested_at, fr.notified
            FROM friend_requests fr
            LEFT JOIN players p1 ON fr.requester_id = p1.user_id
            LEFT JOIN players p2 ON fr.recipient_id = p2.user_id
            WHERE 1=1
        '''
        params = []
        if search:
            query += ' AND (p1.name LIKE ? OR p2.name LIKE ? OR CAST(fr.requester_id AS TEXT) LIKE ? OR CAST(fr.recipient_id AS TEXT) LIKE ?)'
            s = f'%{search}%'
            params += [s, s, s, s]
        query += ' ORDER BY fr.requested_at DESC'
        requests_rows = [dict(r) for r in conn.execute(query, params).fetchall()]

    conn.close()
    rows = friendships if mode == 'friendships' else requests_rows
    return render_template('friends.html', mode=mode, search=search,
                           friendships=friendships, requests=requests_rows,
                           total=len(rows), table_missing=False)


@app.route('/friends/unfriend', methods=['POST'])
def unfriend():
    user_id   = int(request.form.get('user_id', 0))
    friend_id = int(request.form.get('friend_id', 0))
    if not user_id or not friend_id:
        flash('Missing user IDs.', 'error')
        return redirect(url_for('friends'))

    if is_online():
        flash('Unfriend not supported in online mode (use the bot).', 'error')
        return redirect(url_for('friends'))

    conn = get_db()
    conn.execute('DELETE FROM friendships WHERE user_id = ? AND friend_id = ?', (user_id, friend_id))
    conn.execute('DELETE FROM friendships WHERE user_id = ? AND friend_id = ?', (friend_id, user_id))
    conn.commit()
    conn.close()
    flash('Friendship removed (both directions).', 'success')
    return redirect(url_for('friends'))


@app.route('/friends/request/<int:request_id>/delete', methods=['POST'])
def delete_friend_request(request_id):
    if is_online():
        try:
            _rdb().delete('friend_requests', request_id)
            flash('Request deleted.', 'success')
        except Exception as e:
            flash(f'API error: {e}', 'error')
        return redirect(url_for('friends', mode='requests'))

    conn = get_db()
    conn.execute('DELETE FROM friend_requests WHERE request_id = ?', (request_id,))
    conn.commit()
    conn.close()
    flash('Request deleted.', 'success')
    return redirect(url_for('friends', mode='requests'))


# ═══════════════════════════════════════════════════════════════════════════════
# FANTASY CARDS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/fantasy_cards')
def fantasy_cards():
    search = request.args.get('search', '').strip()

    if is_online():
        try:
            rdb = _rdb()
            if not rdb.table_exists('fantasy_cards'):
                return render_template('fantasy_cards.html', rows=[], cols=[],
                                       search='', total=0, table_missing=True)
            rows = rdb.table_rows('fantasy_cards')
            cols = rdb.table_columns('fantasy_cards')
            if search:
                rows = [r for r in rows if any(
                    search.lower() in str(v).lower() for v in r.values() if v is not None
                )]
        except Exception as e:
            flash(f'API error: {e}', 'error')
            rows, cols = [], []
        return render_template('fantasy_cards.html', rows=rows, cols=cols,
                               search=search, total=len(rows), table_missing=False)

    # — Local mode —
    conn = get_db()
    if not table_exists(conn, 'fantasy_cards'):
        conn.close()
        return render_template('fantasy_cards.html', rows=[], cols=[],
                               search='', total=0, table_missing=True)
    cols = [r['name'] for r in conn.execute('PRAGMA table_info(fantasy_cards)').fetchall()]
    query  = 'SELECT * FROM fantasy_cards WHERE 1=1'
    params = []
    if search and cols:
        like_clauses = ' OR '.join(f'CAST({c} AS TEXT) LIKE ?' for c in cols)
        query  += f' AND ({like_clauses})'
        params.extend([f'%{search}%'] * len(cols))
    rows = [dict(r) for r in conn.execute(query, params).fetchall()]
    conn.close()
    return render_template('fantasy_cards.html', rows=rows, cols=cols,
                           search=search, total=len(rows), table_missing=False)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _nullable(val):
    s = (val or '').strip()
    return s if s else None

def _parse_card_form(form):
    return {
        'player_id':   _nullable(form.get('player_id')),
        'name':        form.get('name', '').strip(),
        'attack':      _int(form.get('attack')),
        'defense':     _int(form.get('defense')),
        'speed':       _int(form.get('speed')),
        'height':      _nullable(form.get('height')),
        'club':        _nullable(form.get('club')),
        'position':    _nullable(form.get('position')),
        'overall':     _int(form.get('overall')),
        'image_path':  _nullable(form.get('image_path')),
        'card_rarity': form.get('card_rarity', '').strip(),
        'card_type':   form.get('card_type', 'Normal').strip(),
        'league':      _nullable(form.get('league')),
        'nation':      _nullable(form.get('nation')),
    }


def _validate_card(f):
    if not f['name']:
        return 'Name is required.'
    for stat in ('attack', 'defense', 'speed', 'overall'):
        if f[stat] is None:
            return f'{stat.capitalize()} must be a number.'
        if not (0 <= f[stat] <= 99):
            return f'{stat.capitalize()} must be between 0 and 99.'
    if not f['card_rarity']:
        return 'Rarity is required.'
    if not f['card_type']:
        return 'Card type is required.'
    return None


def _int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# SETTINGS & MODE
# ═══════════════════════════════════════════════════════════════════════════════

@app.context_processor
def inject_mode():
    cfg = load_config()
    return dict(current_mode=cfg['mode'], cfg=cfg)


@app.route('/settings', methods=['GET', 'POST'])
def settings():
    cfg = load_config()
    if request.method == 'POST':
        cfg['mode']    = request.form.get('mode', 'local')
        cfg['api_url'] = request.form.get('api_url', '').strip().rstrip('/')
        cfg['api_key'] = request.form.get('api_key', '').strip()
        save_config(cfg)
        flash('Settings saved.', 'success')
        return redirect(url_for('settings'))
    return render_template('settings.html', cfg=cfg,
                           local_db=os.path.abspath(DB_PATH))


@app.route('/settings/test', methods=['POST'])
def test_connection():
    body    = request.get_json(silent=True) or {}
    api_url = body.get('api_url', '').strip().rstrip('/')
    api_key = body.get('api_key', '').strip()
    if not api_url:
        cfg     = load_config()
        api_url = cfg.get('api_url', '').rstrip('/')
        api_key = cfg.get('api_key', '')
    if not api_url:
        return jsonify(ok=False, msg='No API URL configured.')
    try:
        r = http.get(
            f'{api_url}/api/ping',
            headers={'X-API-Key': api_key},
            timeout=5,
        )
        if r.status_code == 200:
            data = r.json()
            return jsonify(ok=True, msg=f'Connected — bot: {data.get("bot", "ok")}')
        if r.status_code == 401:
            return jsonify(ok=False, msg='Wrong API key.')
        return jsonify(ok=False, msg=f'HTTP {r.status_code}')
    except http.exceptions.ConnectionError:
        return jsonify(ok=False, msg='Connection refused — is the bot running?')
    except http.exceptions.Timeout:
        return jsonify(ok=False, msg='Timed out after 5s.')
    except Exception as e:
        return jsonify(ok=False, msg=str(e))


if __name__ == '__main__':
    print(f'Dashboard → http://localhost:5000')
    print(f'DB        → {os.path.abspath(DB_PATH)}')
    cfg = load_config()
    print(f'Mode      → {cfg["mode"]}')
    app.run(debug=True, port=5000)
