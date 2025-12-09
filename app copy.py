import os
import sqlite3
import re
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash
)
from werkzeug.security import generate_password_hash, check_password_hash

# --------------------------------------------------------
# CONFIG
# --------------------------------------------------------
DB_PATH = "grades.db"

CLASS_COUNT = 15
LAB_COUNT = 15
HW_COUNT = 5       # ตาม requirement เดิม: HW1..HW5
QUIZ_COUNT = 10

app = Flask(__name__)
app.secret_key = "CHANGE_THIS_TO_SOMETHING_RANDOM"


# --------------------------------------------------------
# DB helpers
# --------------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    # -----------------------------
    # courses table
    # -----------------------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS courses (
        course TEXT PRIMARY KEY,
        name   TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('active', 'suspend')),

        -- max scores
        max_total REAL,
        max_mid   REAL,
        max_final REAL,
        max_class REAL,
        max_lab   REAL,
        max_hw    REAL,
        max_quiz  REAL,
        max_p1    REAL,
        max_p2    REAL,

        -- default factors
        class_factor REAL,
        lab_factor   REAL,
        hw_factor    REAL,
        quiz_factor  REAL
    );
    """)

    # -----------------------------
    # scores table (per student)
    # -----------------------------
    # one row per (course, user_id)
    # admin row: (course='All', user_id='admin')
    cols_class = ", ".join([f"class_{i} REAL" for i in range(1, CLASS_COUNT + 1)])
    cols_lab   = ", ".join([f"lab_{i} REAL"   for i in range(1, LAB_COUNT + 1)])
    cols_hw    = ", ".join([f"hw_{i} REAL"    for i in range(1, HW_COUNT + 1)])
    cols_quiz  = ", ".join([f"quiz_{i} REAL"  for i in range(1, QUIZ_COUNT + 1)])

    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS scores (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        course   TEXT NOT NULL,
        user_id  TEXT NOT NULL,
        fullname TEXT NOT NULL,
        password TEXT,
        status   TEXT NOT NULL CHECK (status IN ('active', 'suspend')),

        mid_term REAL,
        final    REAL,
        project1 REAL,
        project2 REAL,

        class_factor REAL,
        lab_factor   REAL,
        hw_factor    REAL,
        quiz_factor  REAL,

        {cols_class},
        {cols_lab},
        {cols_hw},
        {cols_quiz},

        UNIQUE(course, user_id)
    );
    """)

    # ensure admin row exists
    cur.execute(
        "SELECT 1 FROM scores WHERE course='All' AND user_id='admin'"
    )
    if cur.fetchone() is None:
        cur.execute("""
            INSERT INTO scores (course, user_id, fullname, status,
                                class_factor, lab_factor, hw_factor, quiz_factor)
            VALUES ('All', 'admin', 'Administrator', 'active', 1, 1, 1, 1)
        """)

    conn.commit()
    conn.close()


# --------------------------------------------------------
# score computation
# --------------------------------------------------------
def compute_scores(row_dict, course_row):
    """row_dict = แถวจาก scores, course_row = แถวจาก courses"""

    # factor มาจาก course เท่านั้น
    class_factor = course_row.get("class_factor") or 1
    lab_factor   = course_row.get("lab_factor")   or 1
    hw_factor    = course_row.get("hw_factor")    or 1
    quiz_factor  = course_row.get("quiz_factor")  or 1

    # Homework
    hw_vals = [row_dict.get(f"hw_{i}", 0) or 0 for i in range(1, HW_COUNT + 1)]
    hw_sum = sum(hw_vals)
    hw_score = hw_sum / hw_factor if hw_factor else 0.0

    # Quiz
    quiz_vals = [row_dict.get(f"quiz_{i}", 0) or 0 for i in range(1, QUIZ_COUNT + 1)]
    quiz_sum = sum(quiz_vals)
    quiz_score = quiz_sum / quiz_factor if quiz_factor else 0.0

    # Lab
    lab_vals = [row_dict.get(f"lab_{i}", 0) or 0 for i in range(1, LAB_COUNT + 1)]
    lab_sum = sum(lab_vals)
    lab_score = lab_sum / lab_factor if lab_factor else 0.0

    # Class
    class_vals = [row_dict.get(f"class_{i}", 0) or 0 for i in range(1, CLASS_COUNT + 1)]
    class_sum = sum(class_vals)
    class_score = class_sum / class_factor if class_factor else 0.0

    mid = row_dict.get("mid_term") or 0.0
    final = row_dict.get("final") or 0.0
    p1 = row_dict.get("project1") or 0.0
    p2 = row_dict.get("project2") or 0.0

    total = mid + final + p1 + p2 + hw_score + quiz_score + lab_score + class_score

    return {
        "mid_term": mid,
        "final": final,
        "project1": p1,
        "project2": p2,

        "homework_sum": hw_sum,
        "homework_score": hw_score,

        "quiz_sum": quiz_sum,
        "quiz_score": quiz_score,

        "lab_sum": lab_sum,
        "lab_score": lab_score,

        "class_sum": class_sum,
        "class_score": class_score,

        "total": total,
    }


# --------------------------------------------------------
# AUTH / SESSION
# --------------------------------------------------------
@app.route("/")
def index():
    if "role" in session:
        if session["role"] == "admin":
            return redirect(url_for("admin_home"))
        else:
            return redirect(url_for("student_home"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    conn = get_db()
    cur = conn.cursor()

    # สำหรับ dropdown course (เฉพาะ active) + All
    courses = cur.execute(
        "SELECT course, name FROM courses WHERE status='active' ORDER BY course"
    ).fetchall()
    courses = [{"course": "All", "name": "System Admin"}] + [dict(c) for c in courses]

    if request.method == "POST":
        course = request.form.get("course")
        user_id = (request.form.get("user_id") or "").strip()
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")

        # หา row ของ user ใน course นั้น
        row = cur.execute(
            "SELECT * FROM scores WHERE course=? AND user_id=?",
            (course, user_id)
        ).fetchone()

        if row is None:
            conn.close()
            flash("User not found for this course.", "danger")
            return render_template("login.html", courses=courses)

        row = dict(row)

        # ❗ ห้าม student ที่ถูก suspend เข้าระบบ
        # ยกเว้น admin (All/admin) ยังเข้าได้ปกติ
        if not (course == "All" and user_id == "admin"):
            if row.get("status") == "suspend":
                conn.close()
                flash("Your status for this course is suspended. Please contact your instructor.", "danger")
                return render_template("login.html", courses=courses)

        # -------------------------------
        # กรณียังไม่เคยตั้ง password เลย
        # (password เป็น NULL หรือ empty)
        # -------------------------------
        if not row.get("password"):
            # ต้องกรอกทั้งสองช่อง
            if not password or not password_confirm:
                conn.close()
                flash("Please enter your new password twice to set it.", "warning")
                return render_template("login.html", courses=courses)

            # ต้องตรงกัน
            if password != password_confirm:
                conn.close()
                flash("Passwords do not match. Please try again.", "danger")
                return render_template("login.html", courses=courses)

            # ตั้งรหัสใหม่
            hashed = generate_password_hash(password)
            conn.execute(
                "UPDATE scores SET password=? WHERE id=?",
                (hashed, row["id"])
            )
            conn.commit()
            # จากนั้นให้ถือว่า login สำเร็จต่อได้เลย

        else:
            # -------------------------------
            # เคยมี password แล้ว → ใช้ช่อง password ปกติ
            # -------------------------------
            if not password or not check_password_hash(row["password"], password):
                conn.close()
                flash("Invalid password.", "danger")
                return render_template("login.html", courses=courses)

        # -------------------------------
        # Login success
        # -------------------------------
        session.clear()
        if course == "All" and user_id == "admin":
            session["role"] = "admin"
        else:
            session["role"] = "student"
        session["course"] = course
        session["user_id"] = user_id
        session["fullname"] = row["fullname"]

        conn.close()

        if session["role"] == "admin":
            return redirect(url_for("admin_home"))
        else:
            return redirect(url_for("student_home"))

    # GET
    conn.close()
    return render_template("login.html", courses=courses)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# --------------------------------------------------------
# ADMIN – COURSES
# --------------------------------------------------------
def require_admin():
    if session.get("role") != "admin":
        return False
    return True


@app.route("/admin")
def admin_home():
    if not require_admin():
        return redirect(url_for("login"))

    conn = get_db()
    courses = conn.execute(
        "SELECT * FROM courses ORDER BY course"
    ).fetchall()
    conn.close()

    return render_template("admin_home.html", courses=courses)

@app.route("/admin/change_password", methods=["GET", "POST"])
def admin_change_password():
    if not require_admin():
        return redirect(url_for("login"))

    if request.method == "POST":
        old_pw = request.form.get("old_password", "")
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")

        conn = get_db()
        row = conn.execute(
            "SELECT * FROM scores WHERE course='All' AND user_id='admin'"
        ).fetchone()

        if not row:
            conn.close()
            flash("Admin account not found!", "danger")
            return redirect(url_for("admin_change_password"))

        # ตรวจรหัสเก่า
        if row["password"] and not check_password_hash(row["password"], old_pw):
            conn.close()
            flash("Old password is incorrect.", "danger")
            return redirect(url_for("admin_change_password"))

        # ตรวจรหัสใหม่ตรงกัน
        if new_pw != confirm_pw:
            conn.close()
            flash("New passwords do not match.", "warning")
            return redirect(url_for("admin_change_password"))

        # บันทึก
        hashed = generate_password_hash(new_pw)
        conn.execute(
            "UPDATE scores SET password=? WHERE course='All' AND user_id='admin'",
            (hashed,)
        )
        conn.commit()
        conn.close()

        flash("Admin password updated successfully.", "success")
        return redirect(url_for("admin_home"))

    return render_template("admin_change_password.html")


@app.route("/admin/course/add", methods=["POST"])
def admin_add_course():
    if not require_admin():
        return redirect(url_for("login"))

    course = request.form.get("course", "").strip()
    name = request.form.get("name", "").strip()

    max_total = float(request.form.get("max_total") or 100)
    max_mid = float(request.form.get("max_mid") or 0)
    max_final = float(request.form.get("max_final") or 0)
    max_class = float(request.form.get("max_class") or 0)
    max_lab = float(request.form.get("max_lab") or 0)
    max_hw = float(request.form.get("max_hw") or 0)
    max_quiz = float(request.form.get("max_quiz") or 0)
    max_p1 = float(request.form.get("max_p1") or 0)
    max_p2 = float(request.form.get("max_p2") or 0)

    class_factor = float(request.form.get("class_factor") or 1)
    lab_factor = float(request.form.get("lab_factor") or 1)
    hw_factor = float(request.form.get("hw_factor") or 1)
    quiz_factor = float(request.form.get("quiz_factor") or 1)

    if not course or not name:
        flash("Course ID and name are required.", "warning")
        return redirect(url_for("admin_home"))

    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO courses
            (course, name, status,
             max_total, max_mid, max_final, max_class, max_lab, max_hw,
             max_quiz, max_p1, max_p2,
             class_factor, lab_factor, hw_factor, quiz_factor)
            VALUES (?, ?, 'active',
                    ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?)
        """, (
            course, name,
            max_total, max_mid, max_final, max_class, max_lab, max_hw,
            max_quiz, max_p1, max_p2,
            class_factor, lab_factor, hw_factor, quiz_factor
        ))
        conn.commit()
        flash("Course added.", "success")
    except sqlite3.IntegrityError:
        flash("Course ID already exists.", "danger")
    finally:
        conn.close()

    return redirect(url_for("admin_home"))


@app.route("/admin/course/<course_id>/toggle")
def admin_toggle_course(course_id):
    if not require_admin():
        return redirect(url_for("login"))

    conn = get_db()
    row = conn.execute(
        "SELECT status FROM courses WHERE course=?",
        (course_id,)
    ).fetchone()
    if not row:
        conn.close()
        flash("Course not found.", "danger")
        return redirect(url_for("admin_home"))

    status = "active" if row["status"] == "suspend" else "suspend"
    conn.execute(
        "UPDATE courses SET status=? WHERE course=?",
        (status, course_id)
    )
    conn.commit()
    conn.close()
    flash("Course status updated.", "success")
    return redirect(url_for("admin_home"))


@app.route("/admin/course/<course_id>/edit", methods=["GET", "POST"])
def admin_edit_course(course_id):
    if not require_admin():
        return redirect(url_for("login"))

    conn = get_db()
    course = conn.execute(
        "SELECT * FROM courses WHERE course=?",
        (course_id,)
    ).fetchone()

    if not course:
        conn.close()
        flash("Course not found.", "danger")
        return redirect(url_for("admin_home"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        status = request.form.get("status", "active")

        max_total = float(request.form.get("max_total") or 0)
        max_mid = float(request.form.get("max_mid") or 0)
        max_final = float(request.form.get("max_final") or 0)
        max_class = float(request.form.get("max_class") or 0)
        max_lab = float(request.form.get("max_lab") or 0)
        max_hw = float(request.form.get("max_hw") or 0)
        max_quiz = float(request.form.get("max_quiz") or 0)
        max_p1 = float(request.form.get("max_p1") or 0)
        max_p2 = float(request.form.get("max_p2") or 0)

        class_factor = float(request.form.get("class_factor") or 1)
        lab_factor = float(request.form.get("lab_factor") or 1)
        hw_factor = float(request.form.get("hw_factor") or 1)
        quiz_factor = float(request.form.get("quiz_factor") or 1)

        conn.execute("""
            UPDATE courses
            SET name=?, status=?,
                max_total=?, max_mid=?, max_final=?, max_class=?, max_lab=?, max_hw=?,
                max_quiz=?, max_p1=?, max_p2=?,
                class_factor=?, lab_factor=?, hw_factor=?, quiz_factor=?
            WHERE course=?
        """, (
            name, status,
            max_total, max_mid, max_final, max_class, max_lab, max_hw,
            max_quiz, max_p1, max_p2,
            class_factor, lab_factor, hw_factor, quiz_factor,
            course_id
        ))
        conn.commit()
        conn.close()
        flash("Course updated.", "success")
        return redirect(url_for("admin_home"))

    course_dict = dict(course)
    conn.close()
    return render_template("admin_course_edit.html", course=course_dict)


@app.route("/admin/course/<course_id>")
def admin_course(course_id):
    if not require_admin():
        return redirect(url_for("login"))

    conn = get_db()
    course = conn.execute(
        "SELECT * FROM courses WHERE course=?",
        (course_id,),
    ).fetchone()
    if not course:
        conn.close()
        flash("Course not found.", "danger")
        return redirect(url_for("admin_home"))

    rows = conn.execute(
        "SELECT * FROM scores WHERE course=? AND user_id<>'admin' ORDER BY user_id",
        (course_id,),
    ).fetchall()
    conn.close()

    # แปลงเป็น dict ก่อนใช้ทั้งใน compute_scores และ template
    course_dict = dict(course)

    students = []
    for r in rows:
        rd = dict(r)
        sc = compute_scores(rd, course_dict)
        students.append({"row": r, "scores": sc})

    return render_template(
        "admin_course.html",
        course=course_dict,   # <-- แก้จาก course เป็น course_dict
        students=students,
    )


@app.route("/admin/dashboard/<course_id>")
def admin_dashboard(course_id):
    if session.get("role") != "admin":
        return redirect(url_for("login"))

    conn = get_db()

    course = conn.execute(
        "SELECT * FROM courses WHERE course=?", (course_id,)
    ).fetchone()

    rows = conn.execute(
        "SELECT * FROM scores WHERE course=? AND user_id<> 'admin'",
        (course_id,)
    ).fetchall()

    conn.close()

    if not course:
        flash("Course not found.", "danger")
        return redirect(url_for("admin_home"))

    # คำนวณคะแนน
    course_dict = dict(course)
    students = []
    totals = []
    mids = []
    finals = []
    p1s = []
    p2s = []
    class_scores = []
    hw_scores = []
    quiz_scores = []
    lab_scores = []

    for r in rows:
        rd = dict(r)
        sc = compute_scores(rd, course_dict)
        students.append(sc)

        totals.append(sc["total"])
        mids.append(sc["mid_term"])
        finals.append(sc["final"])
        p1s.append(sc["project1"])
        p2s.append(sc["project2"])
        class_scores.append(sc["class_score"])
        hw_scores.append(sc["homework_score"])
        quiz_scores.append(sc["quiz_score"])
        lab_scores.append(sc["lab_score"])

    # ส่งค่าไปให้ Chart.js
    return render_template(
        "admin_dashboard.html",
        course=course_dict,
        totals=totals,
        mids=mids,
        finals=finals,
        p1s=p1s,
        p2s=p2s,
        class_scores=class_scores,
        hw_scores=hw_scores,
        quiz_scores=quiz_scores,
        lab_scores=lab_scores
    )

@app.route("/student/dashboard")
def student_dashboard():
    if session.get("role") != "student":
        return redirect(url_for("login"))

    course_id = session["course"]
    user_id = session["user_id"]

    conn = get_db()
    student = conn.execute(
        "SELECT * FROM scores WHERE course=? AND user_id=?",
        (course_id, user_id)
    ).fetchone()

    course = conn.execute(
        "SELECT * FROM courses WHERE course=?",
        (course_id,)
    ).fetchone()

    conn.close()

    sc = compute_scores(dict(student), dict(course))

    return render_template(
        "student_dashboard.html",
        course=dict(course),
        scores=sc,
        student=dict(student)
    )


# --------------------------------------------------------
# ADMIN – STUDENTS
# --------------------------------------------------------
def _empty_student_for_course(course_row):
    """สร้าง dict ค่าเริ่มต้นตอน add student"""
    d = {
        "user_id": "",
        "fullname": "",
        "status": "active",
        "mid_term": 0.0,
        "final": 0.0,
        "project1": 0.0,
        "project2": 0.0,
        "class_factor": course_row["class_factor"] or 1,
        "lab_factor": course_row["lab_factor"] or 1,
        "hw_factor": course_row["hw_factor"] or 1,
        "quiz_factor": course_row["quiz_factor"] or 1,
    }
    for i in range(1, CLASS_COUNT + 1):
        d[f"class_{i}"] = 0.0
    for i in range(1, LAB_COUNT + 1):
        d[f"lab_{i}"] = 0.0
    for i in range(1, HW_COUNT + 1):
        d[f"hw_{i}"] = 0.0
    for i in range(1, QUIZ_COUNT + 1):
        d[f"quiz_{i}"] = 0.0
    return d

def parse_scores(text, max_count):
    """แปลง string '1 2 3,4' -> list float [1.0,2.0,3.0,4.0] แล้ว padding ด้วย 0 ให้ครบ max_count"""
    if not text:
        return [0.0] * max_count

    tokens = re.split(r"[,\s]+", text.strip())
    nums = []
    for t in tokens:
        if not t:
            continue
        try:
            nums.append(float(t))
        except ValueError:
            nums.append(0.0)

    # เติม 0 ถ้าไม่ครบ
    while len(nums) < max_count:
        nums.append(0.0)

    return nums[:max_count]

def _read_student_from_form(existing=None):
    """อ่านข้อมูลจาก form (ใช้ทั้ง add และ edit)"""
    data = existing.copy() if existing else {}

    # user_id: add อ่านจากฟอร์ม, edit ถ้าไม่มีในฟอร์มให้ใช้ของเดิม
    data["user_id"] = request.form.get("user_id", data.get("user_id", "")).strip()
    data["fullname"] = request.form.get("fullname", data.get("fullname", "")).strip()
    data["status"] = request.form.get("status", data.get("status", "active"))

    data["mid_term"] = float(request.form.get("mid_term") or 0)
    data["final"] = float(request.form.get("final") or 0)
    data["project1"] = float(request.form.get("project1") or 0)
    data["project2"] = float(request.form.get("project2") or 0)

    # factor ยังเก็บไว้ตาม schema เดิม แต่จริง ๆ ใช้ของ course
    data["class_factor"] = existing.get("class_factor", 1) if existing else 1
    data["lab_factor"]   = existing.get("lab_factor", 1)   if existing else 1
    data["hw_factor"]    = existing.get("hw_factor", 1)    if existing else 1
    data["quiz_factor"]  = existing.get("quiz_factor", 1)  if existing else 1

    # อ่านจากช่อง input แนวนอน class_1.., lab_1.., hw_1.., quiz_1..
    for i in range(1, CLASS_COUNT + 1):
        data[f"class_{i}"] = float(request.form.get(f"class_{i}") or 0)

    for i in range(1, LAB_COUNT + 1):
        data[f"lab_{i}"] = float(request.form.get(f"lab_{i}") or 0)

    for i in range(1, HW_COUNT + 1):
        data[f"hw_{i}"] = float(request.form.get(f"hw_{i}") or 0)

    for i in range(1, QUIZ_COUNT + 1):
        data[f"quiz_{i}"] = float(request.form.get(f"quiz_{i}") or 0)

    return data


@app.route("/admin/course/<course_id>/add", methods=["GET", "POST"])
def admin_add_student(course_id):
    if session.get("role") != "admin":
        return redirect(url_for("login"))

    conn = get_db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    if request.method == "POST":
        data = _read_student_from_form(existing={})

        if not data["user_id"] or not data["fullname"]:
            conn.close()
            flash("User ID and Full name are required.", "warning")
            return redirect(url_for("admin_add_student", course_id=course_id))

        # เตรียม column / value สำหรับ INSERT
        columns = [
            "course", "user_id", "fullname", "password", "status",
            "mid_term", "final", "project1", "project2",
            "class_factor", "lab_factor", "hw_factor", "quiz_factor",
        ]
        values = [
            course_id,
            data["user_id"],
            data["fullname"],
            "",  # password เริ่มต้นว่าง
            data["status"],
            data["mid_term"],
            data["final"],
            data["project1"],
            data["project2"],
            data["class_factor"],
            data["lab_factor"],
            data["hw_factor"],
            data["quiz_factor"],
        ]

        # เพิ่ม class_1.., lab_1.., hw_1.., quiz_1..
        for i in range(1, CLASS_COUNT + 1):
            columns.append(f"class_{i}")
            values.append(data[f"class_{i}"])
        for i in range(1, LAB_COUNT + 1):
            columns.append(f"lab_{i}")
            values.append(data[f"lab_{i}"])
        for i in range(1, HW_COUNT + 1):
            columns.append(f"hw_{i}")
            values.append(data[f"hw_{i}"])
        for i in range(1, QUIZ_COUNT + 1):
            columns.append(f"quiz_{i}")
            values.append(data[f"quiz_{i}"])

        placeholders = ", ".join(["?"] * len(columns))
        sql = f"INSERT INTO scores ({', '.join(columns)}) VALUES ({placeholders})"
        cur.execute(sql, values)
        conn.commit()
        conn.close()

        flash("Student added.", "success")
        return redirect(url_for("admin_course", course_id=course_id))

    # GET
    course = cur.execute(
        "SELECT * FROM courses WHERE course = ?", (course_id,)
    ).fetchone()
    conn.close()
    if not course:
        flash("Course not found.", "danger")
        return redirect(url_for("admin_home"))

    return render_template(
        "admin_student_form.html",
        title="Add Student",
        course_id=course_id,
        student={},
        CLASS_COUNT=CLASS_COUNT,
        LAB_COUNT=LAB_COUNT,
        HW_COUNT=HW_COUNT,
        QUIZ_COUNT=QUIZ_COUNT,
)



@app.route("/admin/student/<int:student_id>/edit", methods=["GET", "POST"])
def admin_edit_student(student_id):
    if session.get("role") != "admin":
        return redirect(url_for("login"))

    conn = get_db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    student = cur.execute(
        "SELECT * FROM scores WHERE id = ?", (student_id,)
    ).fetchone()
    if not student:
        conn.close()
        flash("Student not found.", "danger")
        return redirect(url_for("admin_home"))

    course_id = student["course"]

    if request.method == "POST":
        data = _read_student_from_form(existing=dict(student))

        # ตอน edit ไม่ต้องเช็ค user_id (อ่านจาก existing อยู่แล้ว) เช็คแค่ fullname
        if not data["fullname"]:
            conn.close()
            flash("Full name is required.", "warning")
            return redirect(url_for("admin_edit_student", student_id=student_id))

        set_clauses = [
            "user_id = ?",
            "fullname = ?",
            "status = ?",
            "mid_term = ?",
            "final = ?",
            "project1 = ?",
            "project2 = ?",
            "class_factor = ?",
            "lab_factor = ?",
            "hw_factor = ?",
            "quiz_factor = ?",
        ]
        values = [
            data["user_id"],
            data["fullname"],
            data["status"],
            data["mid_term"],
            data["final"],
            data["project1"],
            data["project2"],
            data["class_factor"],
            data["lab_factor"],
            data["hw_factor"],
            data["quiz_factor"],
        ]

        # เพิ่ม class_1.., lab_1.., hw_1.., quiz_1..
        for i in range(1, CLASS_COUNT + 1):
            set_clauses.append(f"class_{i} = ?")
            values.append(data[f"class_{i}"])
        for i in range(1, LAB_COUNT + 1):
            set_clauses.append(f"lab_{i} = ?")
            values.append(data[f"lab_{i}"])
        for i in range(1, HW_COUNT + 1):
            set_clauses.append(f"hw_{i} = ?")
            values.append(data[f"hw_{i}"])
        for i in range(1, QUIZ_COUNT + 1):
            set_clauses.append(f"quiz_{i} = ?")
            values.append(data[f"quiz_{i}"])

        sql = f"UPDATE scores SET {', '.join(set_clauses)} WHERE id = ?"
        values.append(student_id)

        cur.execute(sql, values)
        conn.commit()
        conn.close()

        flash("Student updated.", "success")
        return redirect(url_for("admin_course", course_id=course_id))

    # GET: แสดงฟอร์ม
    conn.close()
    return render_template(
        "admin_student_form.html",
        title="Edit Student",
        course_id=course_id,
        student=dict(student),
        CLASS_COUNT=CLASS_COUNT,
        LAB_COUNT=LAB_COUNT,
        HW_COUNT=HW_COUNT,
        QUIZ_COUNT=QUIZ_COUNT,
    )

@app.route("/admin/student/<int:student_id>/reset_password", methods=["POST"])
def admin_reset_student_password(student_id):
    if not require_admin():
        return redirect(url_for("login"))

    conn = get_db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    row = cur.execute(
        "SELECT course, user_id, fullname FROM scores WHERE id=?",
        (student_id,)
    ).fetchone()

    if not row:
        conn.close()
        flash("Student not found.", "danger")
        return redirect(url_for("admin_home"))

    course_id = row["course"]

    # เคลียร์ password (ให้เป็นค่าว่าง)
    cur.execute(
        "UPDATE scores SET password=NULL WHERE id=?",
        (student_id,)
    )
    conn.commit()
    conn.close()

    flash(f"Password for {row['user_id']} - {row['fullname']} has been reset. "
          f"The student must set a new password on next login.", "success")
    return redirect(url_for("admin_course", course_id=course_id))


@app.route("/admin/student/delete/<int:student_id>", methods=["POST"])
def admin_delete_student(student_id):
    if not require_admin():
        return redirect(url_for("login"))

    conn = get_db()
    row = conn.execute(
        "SELECT course FROM scores WHERE id=?",
        (student_id,)
    ).fetchone()
    if not row:
        conn.close()
        flash("Student not found.", "danger")
        return redirect(url_for("admin_home"))

    course_id = row["course"]
    conn.execute("DELETE FROM scores WHERE id=?", (student_id,))
    conn.commit()
    conn.close()
    flash("Student deleted.", "success")
    return redirect(url_for("admin_course", course_id=course_id))


# --------------------------------------------------------
# STUDENT VIEW
# --------------------------------------------------------
@app.route("/student")
def student_home():
    if session.get("role") != "student":
        return redirect(url_for("login"))

    course_id = session.get("course")
    user_id = session.get("user_id")

    conn = get_db()
    row = conn.execute(
        "SELECT * FROM scores WHERE course=? AND user_id=?",
        (course_id, user_id)
    ).fetchone()
    course = conn.execute(
        "SELECT * FROM courses WHERE course=?",
        (course_id,)
    ).fetchone()
    conn.close()

    if not row or not course:
        flash("No score record found.", "warning")
        return redirect(url_for("login"))

    student = dict(row)
    course_dict = dict(course)
    scores = compute_scores(student, course_dict)


    return render_template(
        "student.html",
        student=student,
        course=course,
        scores=scores,
        CLASS_COUNT=CLASS_COUNT,
        LAB_COUNT=LAB_COUNT,
        HW_COUNT=HW_COUNT,
        QUIZ_COUNT=QUIZ_COUNT
    )


# --------------------------------------------------------
# RUN
# --------------------------------------------------------
if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        # สร้าง DB ใหม่เมื่อยังไม่มี
        init_db()
    else:
        # เผื่อคุณอยากให้แน่ใจว่าตารางถูกสร้างครบ
        init_db()

    app.run(debug=True)
