import sqlite3
from flask import Flask, render_template, redirect, url_for, request, flash, abort, current_app

from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, UserMixin
)
from flask_mail  import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, time, timedelta
from config import Config

app = Flask(__name__)
app.config.from_object(Config)


# ─── Extensions ────────────────────────────────────────────────────────────────
login_manager = LoginManager(app)
login_manager.login_view = 'admin_login'
mail = Mail(app)

    # — initialize our SQLite schema entirely in Python —
with app.app_context():
    conn = sqlite3.connect(app.config['DATABASE'])
    cur  = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS admin (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            username  TEXT    UNIQUE NOT NULL,
            password  TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS customer (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            email       TEXT    UNIQUE NOT NULL,
            created_at  TEXT    NOT NULL
                          DEFAULT (strftime('%d-%m-%Y %H:%M:%S','now','localtime'))
        );
                      
        CREATE TABLE IF NOT EXISTS tables (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            capacity  INTEGER NOT NULL
        );
                      
        CREATE TABLE IF NOT EXISTS booking (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id  INTEGER NOT NULL,
            start_time   TEXT    NOT NULL,
            party_size   INTEGER NOT NULL,
            comments     TEXT,
            FOREIGN KEY(customer_id) REFERENCES customer(id)
        );
        """)
    conn.commit()
    conn.close()

# ─── USER LOADER ───────────────────────────────────────────────────────────────
class Admin(UserMixin):
    def __init__(self, id, username, pw_hash):
        self.id       = id
        self.username = username
        self.password = pw_hash

@login_manager.user_loader
def load_admin(admin_id):
    db = sqlite3.connect(app.config['DATABASE'])
    db.row_factory = sqlite3.Row
    cur = db.cursor()
    cur.execute("SELECT * FROM admin WHERE id = ?", (admin_id,))
    row = cur.fetchone()
    db.close()
    return Admin(row['id'], row['username'], row['password']) if row else None

# ─── ROUTES ───────────────────────────────────────────────────────────────────
OPEN, CLOSE = time(12,0), time(21,0)

@app.route('/')
def site_home():
    return render_template('site/index2.html')

@app.route('/reserve')
def booking_index():
    """Step 1: pick party size & date, then show available slots."""
    size_str = request.args.get('party_size','').strip()
    date_str = request.args.get('date','').strip()

    slots = []
    if size_str.isdigit() and date_str:
        party_size = int(size_str)
        # date_str is "YYYY-MM-DD" from <input type="date">
        chosen_date = datetime.strptime(date_str, "%Y-%m-%d").date()

        # build all 1-hour slots
        all_slots = []
        t = datetime.combine(chosen_date, OPEN)
        end = datetime.combine(chosen_date, CLOSE)
        while t < end:
            all_slots.append(t)
            t += timedelta(hours=1)

        # total seats = sum of table.capacity
        db = sqlite3.connect(current_app.config['DATABASE'])
        cur = db.cursor()
        cur.execute("SELECT SUM(capacity) FROM tables")
        total_seats = cur.fetchone()[0] or 0

        # seats already booked per slot
        cur.execute("""
           SELECT start_time, SUM(party_size) AS taken
             FROM booking
            GROUP BY start_time
        """)
        raw = cur.fetchall()
        booked = {}
        for row in raw:
            ts = datetime.strptime(row[0], "%d-%m-%Y %H:%M:%S")
            if ts.date() == chosen_date:
                booked[ts] = row[1]
        db.close()

        # filter only slots with enough free seats
        slots = [
          s for s in all_slots
          if (total_seats - booked.get(s,0)) >= party_size
        ]

    return render_template(
      'booking/index.html',
      slots=slots,
      selected_size=size_str,
      selected_date=date_str
    )


@app.route('/book', methods=['POST'])
def book():
    name    = request.form['name']
    email   = request.form['email']
    slot    = request.form['time']
    size    = int(request.form['party_size'])
    comments = request.form.get('comments','').strip()

    # validate party size
    if size < 1 or size > 10:
        flash('Party size must be between 1 and 10','error')
        return redirect(url_for('booking_index'))

    start = datetime.strptime(slot, "%Y-%m-%dT%H:%M")
    start_str = start.strftime('%d-%m-%Y %H:%M:%S')

    db = sqlite3.connect(current_app.config['DATABASE'])
    cur = db.cursor()
    # find or create customer
    cur.execute("SELECT id FROM customer WHERE email=?", (email,))
    row = cur.fetchone()
    if row:
        cust_id = row[0]
    else:
        created = datetime.utcnow().strftime('%d-%m-%Y %H:%M:%S')
        cur.execute(
          "INSERT INTO customer(name,email,created_at) VALUES(?,?,?)",
          (name,email,created)
        )
        cust_id = cur.lastrowid

    # insert booking
    cur.execute(
      "INSERT INTO booking(customer_id,start_time,party_size, comments) VALUES(?,?,?,?)",
      (cust_id, start_str, size, comments)
    )
    db.commit()
    db.close()

    # confirmation email
    msg = Message("Your Reservation", recipients=[email])
    msg.body = f"Hi {name}, your table is booked for {start.strftime('%d-%m-%Y %H:%M')} for {size} people."
    mail.send(msg)

    return redirect(url_for('thankyou'))


@app.route('/thankyou')
def thankyou():
    return render_template('booking/thankyou.html')


# ——— ADMIN ROUTES ———————————————————————————————————————————————

@app.route('/admin/login', methods=['GET','POST'])
def admin_login():
    if request.method=='POST':
        user, pw = request.form['username'], request.form['password']
        db = sqlite3.connect(current_app.config['DATABASE'])
        db.row_factory = sqlite3.Row
        cur = db.cursor()
        cur.execute("SELECT * FROM admin WHERE username=?", (user,))
        row = cur.fetchone()
        db.close()

        if row and check_password_hash(row['password'], pw):
            admin = Admin(row['id'], row['username'], row['password'])
            login_user(admin)
            return redirect(url_for('admin_bookings'))

        flash('Invalid credentials.','error')

    return render_template('admin/admin_login.html')


@app.route('/admin/logout')
@login_required
def admin_logout():
    logout_user()
    return redirect(url_for('admin_login'))


@app.route('/admin/bookings')
@login_required
def admin_bookings():
    db = sqlite3.connect(current_app.config['DATABASE'])
    db.row_factory = sqlite3.Row
    cur = db.cursor()
    cur.execute("""
      SELECT b.id,
             c.name AS name, 
             c.email AS email, 
             b.start_time, 
             b.party_size,
             b.comments
        FROM booking b
        JOIN customer c ON c.id=b.customer_id
     ORDER BY b.start_time
    """)
    rows = cur.fetchall()
    db.close()

    # build a list of plain dicts, converting start_time → datetime
    bookings = []
    for r in rows:
        bookings.append({
            'id':         r['id'],
            'name':       r['name'],
            'email':      r['email'],
            'start_time': datetime.strptime(r['start_time'], '%d-%m-%Y %H:%M:%S'),
            'party_size': r['party_size'],
            'comments'   : r['comments'] or ''
        })

    return render_template('admin/admin_bookings.html', bookings=bookings)


@app.route('/admin/bookings/create', methods=['GET','POST'])
@login_required
def admin_booking_create():
    if request.method=='POST':
        name, email = request.form['name'], request.form['email']
        dt, size    = request.form['datetime'], int(request.form['party_size'])
        start = datetime.strptime(dt, "%Y-%m-%dT%H:%M")
        start_str = start.strftime('%d-%m-%Y %H:%M:%S')
        comments   = request.form.get('comments','').strip()

        # enforce hours
        if not (OPEN <= start.time() < CLOSE):
            flash(f'Bookings only between {OPEN:%H:%M}–{CLOSE:%H:%M}.','error')
            return render_template('admin/admin_booking_form.html', booking=None)

        db = sqlite3.connect(current_app.config['DATABASE'])
        cur = db.cursor()
        # customer lookup/insert
        cur.execute("SELECT id FROM customer WHERE email=?", (email,))
        r = cur.fetchone()
        if r:
            cid = r[0]
        else:
            created = datetime.utcnow().strftime('%d-%m-%Y %H:%M:%S')
            cur.execute(
              "INSERT INTO customer(name,email,created_at) VALUES(?,?,?)",
              (name,email,created)
            )
            cid = cur.lastrowid

        # insert booking
        cur.execute(
          "INSERT INTO booking(customer_id,start_time,party_size, comments) VALUES(?,?,?,?)",
          (cid, start_str, size, comments)
        )
        db.commit()
        db.close()

        # send confirmation
        msg = Message(
          "Your Crisp Reservation is Confirmed",
          recipients=[email]
        )
        msg.body = (
          f"Hi {name},\n\n"
          f"Your table has been booked for {start:%d-%m-%Y %H:%M} "
          f"for {size} people.\n\n"
          "– Crisp Burger"
        )
        mail.send(msg)

        return redirect(url_for('admin_bookings'))

    return render_template('admin/admin_booking_form.html', booking=None)


@app.route('/admin/bookings/edit/<int:id>', methods=['GET','POST'])
@login_required
def admin_booking_edit(id):
    db = sqlite3.connect(current_app.config['DATABASE'])
    db.row_factory = sqlite3.Row
    cur = db.cursor()

    # load existing
    cur.execute("""
      SELECT b.id, 
             b.start_time, 
             b.party_size, 
             c.name AS name, 
             c.email AS email
        FROM booking b
        JOIN customer c ON c.id=b.customer_id
       WHERE b.id=?
    """, (id,))
    row = cur.fetchone()
    if not row:
        abort(404)

    if request.method=='POST':
        new_dt   = datetime.strptime(request.form['datetime'], "%Y-%m-%dT%H:%M")
        new_dt_str = new_dt.strftime('%d-%m-%Y %H:%M:%S')
        new_size = int(request.form['party_size'])
        comments    = request.form.get('comments','').strip()

        cur.execute(
          "UPDATE booking SET start_time=?, party_size=?, comments=? WHERE id=?",
          (new_dt_str, new_size, comments, id)
        )
        db.commit()

        # notify customer
        msg = Message(
          "Your Crisp Reservation has Been Updated",
          recipients=[row['email']]
        )
        msg.body = (
          f"Hi {row['name']},\n\n"
          f"Your reservation has been updated to "
          f"{new_dt:%d-%m-%Y %H:%M} for {new_size} people.\n\n"
          "– Crisp Burger"
        )
        mail.send(msg)

        return redirect(url_for('admin_bookings'))
    
    db.close()

    booking = {
        'id':         row['id'],
        'name':       row['name'],
        'email':      row['email'],
        'start_time': datetime.strptime(row['start_time'], "%d-%m-%Y %H:%M:%S"),
        'party_size': row['party_size'],
    }
    # pass row to template as `booking`
    return render_template('admin/admin_booking_form.html', booking=booking)


@app.route('/admin/bookings/delete/<int:id>', methods=['POST'])
@login_required
def admin_booking_delete(id):
    db = sqlite3.connect(current_app.config['DATABASE'])
    db.row_factory = sqlite3.Row
    cur = db.cursor()

    # fetch details for email
    cur.execute("""
      SELECT b.start_time, b.party_size, c.name, c.email
        FROM booking b JOIN customer c ON c.id=b.customer_id
       WHERE b.id=?
    """, (id,))
    row = cur.fetchone()
    if not row:
        db.close()
        abort(404)

    # then delete
    cur.execute("DELETE FROM booking WHERE id=?", (id,))
    db.commit()
    db.close()

    # send cancellation
    # parse the stored text into a datetime
    ts = datetime.strptime(row['start_time'], "%d-%m-%Y %H:%M:%S")
    human = ts.strftime("%d-%m-%Y %H:%M")
    msg = Message(
      "Your Crisp Reservation has Been Cancelled",
      recipients=[row['email']]
    )
    msg.body = (
      f"Hi {row['name']},\n\n"
      f"Your reservation for {human} "
      "has been cancelled.\n\n"
      "– Crisp Burger"
    )
    mail.send(msg)

    return redirect(url_for('admin_bookings'))


@app.route('/admin/tables')
@login_required
def admin_tables():
    db = sqlite3.connect(current_app.config['DATABASE'])
    db.row_factory = sqlite3.Row
    cur = db.cursor()
    cur.execute("SELECT * FROM tables ORDER BY capacity")
    tables = cur.fetchall()
    db.close()
    return render_template('admin/admin_tables.html', tables=tables)


@app.route('/admin/tables/create', methods=['GET','POST'])
@login_required
def admin_table_create():
    if request.method=='POST':
        cap = int(request.form['capacity'])
        db = sqlite3.connect(current_app.config['DATABASE'])
        cur = db.cursor()
        cur.execute("INSERT INTO tables(capacity) VALUES(?)", (cap,))
        db.commit()
        db.close()
        return redirect(url_for('admin_tables'))
    return render_template('admin/admin_table_form.html', table=None)


@app.route('/admin/tables/edit/<int:id>', methods=['GET','POST'])
@login_required
def admin_table_edit(id):
    db = sqlite3.connect(current_app.config['DATABASE'])
    db.row_factory = sqlite3.Row
    cur = db.cursor()
    cur.execute("SELECT * FROM tables WHERE id=?", (id,))
    row = cur.fetchone()
    if not row:
        db.close()
        abort(404)

    if request.method=='POST':
        new_cap = int(request.form['capacity'])
        cur.execute("UPDATE tables SET capacity=? WHERE id=?", (new_cap, id))
        db.commit()
        db.close()
        return redirect(url_for('admin_tables'))

    db.close()
    return render_template('admin/admin_table_form.html', table=row)


@app.route('/admin/tables/delete/<int:id>', methods=['POST'])
@login_required
def admin_table_delete(id):
    db = sqlite3.connect(current_app.config['DATABASE'])
    cur = db.cursor()
    cur.execute("DELETE FROM tables WHERE id=?", (id,))
    db.commit()
    db.close()
    return redirect(url_for('admin_tables'))



if __name__ == '__main__':
    # Starts Flask’s built-in web server in debug mode
    app.run(host='0.0.0.0', port=5000,debug=True)
