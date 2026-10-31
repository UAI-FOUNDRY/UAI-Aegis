"""BML (Basics of Machine Learning) lab: one ready-made question per practical of the syllabus.

    python -m app.cli bml-lab                 # against http://localhost:8000 (server must run)

Everything goes through the normal professor API (create subject, questions, tests or
datasets, generate outputs, validate), exactly as a professor would in the portal, and each
question's reference solution is validated at the end. Answers are written in 'python3-ml'
(NumPy, pandas, matplotlib, seaborn, scikit-learn). Datasets are synthetic and generated
from fixed seeds, so the lab is the same every time. Safe to run again: an existing
subject is left alone.

Practical 4 ("types of machine learning and their applications") is theory and has no
coding question; give it as a written assignment.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from typing import Any

SUBJECT_CODE = "DEMO-BML-LAB"
PROFESSOR = "prof.demo@aegis.local"
PASSWORD = "Aegis-demo-2026!"
BATCH_CODE = "CSE-AIML-2025-A"
TIME_LIMIT_MS = 8000  # importing pandas + scikit-learn takes a few seconds
MEMORY_KB = 512000  # numpy/pandas/scikit-learn need more than the default; = Judge0's MAX_MEMORY_LIMIT

# ------------------------------------------------------------------------- datasets


def _csv(header: list[str], rows: list[list[Any]]) -> str:
    return "\n".join([",".join(header)] + [",".join(str(v) for v in r) for r in rows]) + "\n"


def marks_csv(seed: int, n: int) -> str:
    rng = random.Random(seed)
    rows = []
    for i in range(1, n + 1):
        rows.append([f"S{i:03d}", rng.randint(35, 100), rng.randint(30, 100), round(rng.uniform(40, 99), 1),
                     rng.randint(55, 100)])
    return _csv(["student_id", "maths", "physics", "chemistry", "attendance"], rows)


def preprocessing_input(seed: int) -> str:
    rng = random.Random(seed)
    cities = ["pune", " Mumbai", "NAGPUR ", "pune ", "  nashik", "Mumbai"]
    students = []
    for i in range(1, 13):
        marks = "" if rng.random() < 0.25 else rng.randint(30, 98)
        students.append([i, f"Student{i}", rng.choice(cities).replace(",", ""), marks])
    students.append(list(students[2]))  # exact duplicate rows
    students.append(list(students[5]))
    rng.shuffle(students)
    fees = [[i, rng.choice(["yes", "no"])] for i in range(1, 13) if i % 5 != 0]  # some students have no fee record
    return _csv(["id", "name", "city", "marks"], students) + "---\n" + _csv(["id", "fee_paid"], fees)


def sales_csv(seed: int) -> str:
    rng = random.Random(seed)
    rows = []
    for month in ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]:
        for region in ["North", "South", "East", "West"]:
            rows.append([month, region, rng.randint(80, 400), rng.randint(5, 40)])
    return _csv(["month", "region", "sales", "returns"], rows)


def flowers_csv(seed: int, n: int) -> str:
    rng = random.Random(seed)
    centres = {"setosa": (5.0, 3.4, 1.5, 0.25), "versicolor": (5.9, 2.8, 4.3, 1.3), "virginica": (6.6, 3.0, 5.6, 2.0)}
    rows = []
    for _ in range(n):
        species = rng.choice(list(centres))
        c = centres[species]
        rows.append([round(rng.gauss(c[0], 0.35), 1), round(rng.gauss(c[1], 0.3), 1),
                     round(rng.gauss(c[2], 0.35), 1), round(abs(rng.gauss(c[3], 0.18)), 1), species])
    return _csv(["sepal_length", "sepal_width", "petal_length", "petal_width", "species"], rows)


def loans_csv(seed: int, n: int) -> str:
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        income = rng.randint(15, 150)  # thousands per month
        score = rng.randint(450, 850)
        amount = rng.randint(50, 2500)  # thousands
        job = rng.choice(["salaried", "self-employed", "student"])
        # Higher loan-to-income ratio and lower credit score mean more risk; roughly half are approved.
        risk = (1.8 * math.log(amount / (income * 12)) - (score - 650) / 70
                + {"salaried": -0.8, "self-employed": 0.0, "student": 1.0}[job])
        approved = "yes" if risk + rng.gauss(0, 0.5) < 0 else "no"
        rows.append([income, score, amount, job, approved])
    return _csv(["monthly_income", "credit_score", "loan_amount", "employment", "approved"], rows)


def salary_csv(seed: int, n: int) -> str:
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        years = round(rng.uniform(0, 20), 1)
        rows.append([years, round(30000 + 9000 * years + rng.gauss(0, 9000))])
    return _csv(["years_experience", "salary"], rows)


def houses_csv(seed: int, n: int) -> str:
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        area = rng.randint(400, 3000)
        bedrooms = rng.randint(1, 5)
        age = rng.randint(0, 40)
        distance = round(rng.uniform(1, 30), 1)
        price = 20 + 0.045 * area + 6 * bedrooms - 0.6 * age - 1.4 * distance + rng.gauss(0, 6)
        rows.append([area, bedrooms, age, distance, round(price, 1)])
    return _csv(["area_sqft", "bedrooms", "age_years", "distance_km", "price_lakh"], rows)


def exam_pass_csv(seed: int, n: int) -> str:
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        hours = round(rng.uniform(0, 12), 1)
        attendance = rng.randint(40, 100)
        assignments = rng.randint(0, 10)
        z = 2.5 * (-9 + 0.55 * hours + 0.06 * attendance + 0.35 * assignments)  # steep: labels mostly follow the rule
        passed = int(rng.random() < 1 / (1 + math.exp(-z)))
        rows.append([hours, attendance, assignments, passed])
    return _csv(["hours_studied", "attendance", "assignments_done", "passed"], rows)


def crops_csv(seed: int, n: int) -> str:
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        ph = round(rng.uniform(4, 9), 2)
        rain = rng.randint(20, 300)
        temp = rng.randint(10, 40)
        suitable = int(6 <= ph <= 7.5 and 60 <= rain <= 180 and temp >= 18)
        if rng.random() < 0.03:
            suitable = 1 - suitable
        rows.append([ph, rain, temp, suitable])
    return _csv(["soil_ph", "rainfall_mm", "temperature_c", "suitable"], rows)


def churn_csv(seed: int, n: int) -> str:
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        tenure = rng.randint(1, 72)
        charges = rng.randint(200, 2000)
        calls = rng.randint(0, 10)
        complaints = rng.randint(0, 5)
        usage = round(rng.uniform(0, 100), 1)
        age = rng.randint(18, 70)
        data_gb = round(rng.uniform(0, 50), 1)
        contract = rng.choice([0, 1, 2])
        score = (complaints * 0.9 + calls * 0.3 - tenure / 18 + (charges > 1300) * 1.2 - contract * 0.8
                 + (usage < 20) * 1.0 + rng.gauss(0, 0.9))
        rows.append([tenure, charges, calls, complaints, usage, age, data_gb, contract, int(score > 1.0)])
    return _csv(["tenure_months", "monthly_charges", "support_calls", "complaints", "usage_pct", "age",
                 "data_gb", "contract_years", "churned"], rows)


def sensors_csv(seed: int, n: int) -> str:
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        faulty = rng.random() < 0.4
        if faulty:
            temp, vib, noise = rng.gauss(78, 7), rng.gauss(6.5, 1.5), rng.gauss(70, 6)
        else:
            temp, vib, noise = rng.gauss(62, 7), rng.gauss(3.5, 1.4), rng.gauss(58, 6)
        rows.append([round(temp, 1), round(vib, 2), round(noise, 1), int(faulty)])
    return _csv(["temperature", "vibration", "noise_db", "faulty"], rows)


def diabetes_like_csv(seed: int, n: int) -> str:
    rng = random.Random(seed)
    rows = []
    weights = [3.0, -2.0, 1.5, 0, 0, 0.8, 0, 0, -1.2, 0, 0, 0, 0.5, 0, 0, 0, 0, 0, 0, 0]
    for _ in range(n):
        xs = [round(rng.gauss(0, 1), 3) for _ in range(20)]
        y = 50 + sum(w * x for w, x in zip(weights, xs, strict=True)) * 4 + rng.gauss(0, 3)
        rows.append(xs + [round(y, 2)])
    return _csv([f"f{i}" for i in range(1, 21)] + ["progression"], rows)


def labels(seed: int, n: int, flip: float) -> list[list[int]]:
    rng = random.Random(seed)
    y_true = [int(rng.random() < 0.45) for _ in range(n)]
    y_pred = [y if rng.random() > flip else 1 - y for y in y_true]
    return [y_true, y_pred]


def cv_csv(seed: int, n: int) -> str:
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        a, b, c = round(rng.gauss(0, 1), 3), round(rng.gauss(0, 1), 3), round(rng.gauss(0, 1), 3)
        rows.append([a, b, c, int(1.4 * a - b + 0.4 * c + rng.gauss(0, 0.7) > 0)])
    return _csv(["x1", "x2", "x3", "label"], rows)


# ------------------------------------------------------------------- the questions

@dataclass
class Spec:
    practical: str
    title: str
    statement: str
    grader: str
    config: dict[str, Any]
    reference: str
    difficulty: str = "medium"
    tags: list[str] = field(default_factory=list)
    marks: int = 10
    tests: list[tuple[str, bool]] = field(default_factory=list)  # (input, sample) for non-ML graders
    datasets: list[tuple[str, str, bool]] = field(default_factory=list)  # (train, test, sample) for ML
    starter: str | None = None


ML_HOW_TO = ("\n\n**How to answer:** write a function `solve(train_csv, test_csv)`. `train_csv` is the training "
             "data as CSV text (with the target column); `test_csv` has the same columns **without** the target. "
             "Return one prediction per row of `test_csv` (e.g. `list(model.predict(X_test))`). Read them with "
             "`pd.read_csv(io.StringIO(train_csv))`.")


def _ml(practical, title, statement, config, reference, make, tags, difficulty="medium", n_train=240, n_test=90):
    # No starter code: students' editors start empty (the team's rule); the statement explains solve().
    datasets = [(make(seed, n_train), make(seed + 1000, n_test), seed == 11) for seed in (11, 12, 13)]
    return Spec(practical, title, statement + ML_HOW_TO, "ml", config, reference, difficulty, tags, datasets=datasets)


SPECS: list[Spec] = [
    Spec(
        "1", "Exp 1 · Check the ML environment",
        "Experiment 1: confirm your Python ML environment works.\n\n"
        "Standard input is a CSV of student marks with columns `student_id, maths, physics, chemistry, attendance`.\n\n"
        "Using **pandas** (and NumPy if you like), print:\n"
        "1. `rows: R, columns: C`\n"
        "2. then, for each numeric column in order, `column: mean` with the mean rounded to 2 decimals.\n\n"
        "Example line: `maths: 67.45`",
        "stdio", {"compare": "lines"},
        """import sys
import numpy as np
import pandas as pd

df = pd.read_csv(sys.stdin)
print(f"rows: {df.shape[0]}, columns: {df.shape[1]}")
for column in df.select_dtypes(include=np.number).columns:
    print(f"{column}: {df[column].mean():.2f}")
""",
        "easy", ["environment", "pandas", "numpy"],
        tests=[(marks_csv(1, 8), True), (marks_csv(2, 25), False), (marks_csv(3, 60), False)],
    ),
    Spec(
        "2", "Exp 2 · Clean, transform and integrate data",
        "Experiment 2: data pre-processing with **pandas**.\n\n"
        "Standard input has two CSV tables separated by a line `---`:\n"
        "`students(id, name, city, marks)` and `fees(id, fee_paid)`.\n\n"
        "1. **Clean:** drop exact duplicate rows; in `city` strip spaces and use Title Case; fill missing `marks` "
        "with the **median** of `marks` (after removing duplicates) and round marks to 1 decimal.\n"
        "2. **Transform:** add `grade`: `A` if marks ≥ 75, `B` if marks ≥ 50, otherwise `C`.\n"
        "3. **Integrate:** inner-join with `fees` on `id`, sort by `id`.\n\n"
        "Print the result as CSV without the index: `print(df.to_csv(index=False), end=\"\")`.",
        "stdio", {"compare": "lines"},
        """import io
import sys
import pandas as pd

students_text, fees_text = sys.stdin.read().split("---\\n")
students = pd.read_csv(io.StringIO(students_text))
fees = pd.read_csv(io.StringIO(fees_text))

students = students.drop_duplicates()
students["city"] = students["city"].str.strip().str.title()
students["marks"] = students["marks"].fillna(students["marks"].median()).round(1)
students["grade"] = students["marks"].apply(lambda m: "A" if m >= 75 else ("B" if m >= 50 else "C"))

result = students.merge(fees, on="id").sort_values("id")
print(result.to_csv(index=False), end="")
""",
        "easy", ["pre-processing", "pandas"],
        tests=[(preprocessing_input(21), True), (preprocessing_input(22), False), (preprocessing_input(23), False)],
    ),
    Spec(
        "3", "Exp 3 · Visualize sales data (EDA)",
        "Experiment 3: exploratory data analysis with **matplotlib** and **seaborn**.\n\n"
        "The file `data.csv` has columns `month, region, sales, returns`. Draw:\n"
        "1. A **bar chart** of total `sales` per `region` with title **Total Sales by Region**, x-axis label "
        "**Region** and y-axis label **Sales**.\n"
        "2. A **histogram** of `sales` using seaborn (`sns.histplot`).\n\n"
        "Read the data with `pd.read_csv(\"data.csv\")`. `plt.show()` is fine; nothing is displayed.",
        "plot", {"data_csv": sales_csv(31), "partial_credit": True},
        """import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

df = pd.read_csv("data.csv")
totals = df.groupby("region")["sales"].sum()

plt.figure(figsize=(6, 4))
plt.bar(totals.index, totals.values, color="steelblue")
plt.title("Total Sales by Region")
plt.xlabel("Region")
plt.ylabel("Sales")
plt.show()

plt.figure(figsize=(6, 4))
sns.histplot(df["sales"], bins=10)
plt.title("Distribution of Sales")
plt.show()
""",
        "easy", ["visualization", "matplotlib", "seaborn"],
        tests=[("chart bar\ntitle contains total sales", True),
               ("xlabel = Region\nylabel = Sales\nxticks include East, North, South, West\ncount bars >= 4", False),
               ("chart hist\ncount figures >= 2", False)],
    ),
    _ml("5", "Exp 5 · ML workflow on the flowers dataset",
        "Experiment 5: implement the full ML workflow with **scikit-learn**: load → split features/target → "
        "scale → train → predict.\n\nPredict `species` (setosa / versicolor / virginica) from the four measurements. "
        "Full marks at accuracy **0.90**, half marks at **0.75**.",
        {"task": "classification", "target": "species", "metric": "accuracy",
         "thresholds": [{"at": 0.9, "credit": 1.0}, {"at": 0.75, "credit": 0.5}]},
        """import io
import pandas as pd
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

def solve(train_csv, test_csv):
    train = pd.read_csv(io.StringIO(train_csv))
    test = pd.read_csv(io.StringIO(test_csv))
    X, y = train.drop(columns="species"), train["species"]
    model = make_pipeline(StandardScaler(), KNeighborsClassifier(n_neighbors=5))
    model.fit(X, y)
    return list(model.predict(test[X.columns]))
""", flowers_csv, ["workflow", "classification"]),
    _ml("6", "Exp 6 · Loan approval application",
        "Experiment 6: build a small ML application. Pre-process the data (note `employment` is a **text** "
        "category: encode it), train a model and predict `approved` (yes / no).\n\nScored by macro **F1**: "
        "full marks at **0.80**, half marks at **0.65**.",
        {"task": "classification", "target": "approved", "metric": "f1_macro",
         "thresholds": [{"at": 0.8, "credit": 1.0}, {"at": 0.65, "credit": 0.5}]},
        """import io
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

def solve(train_csv, test_csv):
    train = pd.read_csv(io.StringIO(train_csv))
    test = pd.read_csv(io.StringIO(test_csv))
    y = train.pop("approved")
    both = pd.get_dummies(pd.concat([train, test]), columns=["employment"], dtype=float)
    X_train, X_test = both.iloc[:len(train)], both.iloc[len(train):]
    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))
    model.fit(X_train, y)
    return list(model.predict(X_test))
""", loans_csv, ["application", "pre-processing", "encoding"]),
    _ml("7", "Exp 7 · Simple linear regression: salary",
        "Experiment 7: implement **simple linear regression** with scikit-learn to predict `salary` from "
        "`years_experience`.\n\nScored by **R²**: full marks at **0.90**, half marks at **0.75**.",
        {"task": "regression", "target": "salary", "metric": "r2",
         "thresholds": [{"at": 0.9, "credit": 1.0}, {"at": 0.75, "credit": 0.5}]},
        """import io
import pandas as pd
from sklearn.linear_model import LinearRegression

def solve(train_csv, test_csv):
    train = pd.read_csv(io.StringIO(train_csv))
    test = pd.read_csv(io.StringIO(test_csv))
    model = LinearRegression()
    model.fit(train[["years_experience"]], train["salary"])
    return [float(p) for p in model.predict(test[["years_experience"]])]
""", salary_csv, ["linear regression"], difficulty="easy"),
    _ml("8", "Exp 8 · Multiple linear regression: house prices",
        "Experiment 8: implement **multiple linear regression** on `area_sqft, bedrooms, age_years, distance_km` "
        "to predict `price_lakh`. Print your model's R², MAE and RMSE on a validation split (they appear in your "
        "run log) and explain them in your write-up.\n\nScored by **R²** on hidden data: full marks at **0.90**, "
        "half marks at **0.75**.",
        {"task": "regression", "target": "price_lakh", "metric": "r2",
         "thresholds": [{"at": 0.9, "credit": 1.0}, {"at": 0.75, "credit": 0.5}]},
        """import io
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split

def solve(train_csv, test_csv):
    train = pd.read_csv(io.StringIO(train_csv))
    test = pd.read_csv(io.StringIO(test_csv))
    X, y = train.drop(columns="price_lakh"), train["price_lakh"]
    X_tr, X_val, y_tr, y_val = train_test_split(X, y, test_size=0.2, random_state=0)
    check = LinearRegression().fit(X_tr, y_tr).predict(X_val)
    print("R2", r2_score(y_val, check), "MAE", mean_absolute_error(y_val, check),
          "RMSE", mean_squared_error(y_val, check) ** 0.5)
    model = LinearRegression().fit(X, y)
    return [float(p) for p in model.predict(test[X.columns])]
""", houses_csv, ["linear regression", "metrics"]),
    _ml("9", "Exp 9 · Logistic regression: will the student pass?",
        "Experiment 9: implement **logistic regression** for binary classification. Predict `passed` (0 / 1) from "
        "`hours_studied, attendance, assignments_done`.\n\nScored by **accuracy**: full marks at **0.80**, half "
        "marks at **0.70**.",
        {"task": "classification", "target": "passed", "metric": "accuracy",
         "thresholds": [{"at": 0.8, "credit": 1.0}, {"at": 0.7, "credit": 0.5}]},
        """import io
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

def solve(train_csv, test_csv):
    train = pd.read_csv(io.StringIO(train_csv))
    test = pd.read_csv(io.StringIO(test_csv))
    X, y = train.drop(columns="passed"), train["passed"]
    model = make_pipeline(StandardScaler(), LogisticRegression())
    model.fit(X, y)
    return [int(p) for p in model.predict(test[X.columns])]
""", exam_pass_csv, ["logistic regression", "binary classification"]),
    _ml("10", "Exp 10 · Decision tree: crop suitability",
        "Experiment 10: implement a **Decision Tree Classifier**. Predict whether land is `suitable` (0 / 1) for a "
        "crop from `soil_ph, rainfall_mm, temperature_c`. (The rule is made of thresholds, which trees learn well; "
        "a linear model will struggle.)\n\nScored by **accuracy**: full marks at **0.90**, half marks at **0.80**.",
        {"task": "classification", "target": "suitable", "metric": "accuracy",
         "thresholds": [{"at": 0.9, "credit": 1.0}, {"at": 0.8, "credit": 0.5}]},
        """import io
import pandas as pd
from sklearn.tree import DecisionTreeClassifier

def solve(train_csv, test_csv):
    train = pd.read_csv(io.StringIO(train_csv))
    test = pd.read_csv(io.StringIO(test_csv))
    X, y = train.drop(columns="suitable"), train["suitable"]
    model = DecisionTreeClassifier(max_depth=6, random_state=0)
    model.fit(X, y)
    return [int(p) for p in model.predict(test[X.columns])]
""", crops_csv, ["decision tree"], n_train=400, n_test=120),
    _ml("11", "Exp 11 · Random forest vs decision tree: customer churn",
        "Experiment 11: implement **Random Forest** and compare it with a **Decision Tree** on the churn data "
        "(predict `churned`, 0 / 1). Print both models' validation accuracy (it appears in your run log) and submit "
        "the better model's predictions.\n\nScored by **accuracy**: full marks at **0.80**, half marks at **0.70**.",
        {"task": "classification", "target": "churned", "metric": "accuracy",
         "thresholds": [{"at": 0.8, "credit": 1.0}, {"at": 0.7, "credit": 0.5}]},
        """import io
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.tree import DecisionTreeClassifier

def solve(train_csv, test_csv):
    train = pd.read_csv(io.StringIO(train_csv))
    test = pd.read_csv(io.StringIO(test_csv))
    X, y = train.drop(columns="churned"), train["churned"]
    X_tr, X_val, y_tr, y_val = train_test_split(X, y, test_size=0.25, random_state=0)
    tree = DecisionTreeClassifier(random_state=0).fit(X_tr, y_tr)
    forest = RandomForestClassifier(n_estimators=200, random_state=0).fit(X_tr, y_tr)
    print("decision tree", tree.score(X_val, y_val), "random forest", forest.score(X_val, y_val))
    model = RandomForestClassifier(n_estimators=200, random_state=0).fit(X, y)
    return [int(p) for p in model.predict(test[X.columns])]
""", churn_csv, ["random forest", "decision tree", "comparison"], difficulty="hard", n_train=400, n_test=120),
    _ml("12", "Exp 12 · Naive Bayes and SVM: machine fault detection",
        "Experiment 12: implement **Gaussian Naive Bayes** and a **Support Vector Machine** to detect `faulty` "
        "machines (0 / 1) from `temperature, vibration, noise_db`. Compare them on a validation split and submit "
        "the better model's predictions.\n\nScored by macro **F1**: full marks at **0.85**, half marks at **0.70**.",
        {"task": "classification", "target": "faulty", "metric": "f1_macro",
         "thresholds": [{"at": 0.85, "credit": 1.0}, {"at": 0.7, "credit": 0.5}]},
        """import io
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.naive_bayes import GaussianNB
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

def solve(train_csv, test_csv):
    train = pd.read_csv(io.StringIO(train_csv))
    test = pd.read_csv(io.StringIO(test_csv))
    X, y = train.drop(columns="faulty"), train["faulty"]
    X_tr, X_val, y_tr, y_val = train_test_split(X, y, test_size=0.25, random_state=0)
    nb = GaussianNB().fit(X_tr, y_tr)
    svm = make_pipeline(StandardScaler(), SVC(kernel="rbf")).fit(X_tr, y_tr)
    print("naive bayes", nb.score(X_val, y_val), "svm", svm.score(X_val, y_val))
    model = make_pipeline(StandardScaler(), SVC(kernel="rbf")).fit(X, y)
    return [int(p) for p in model.predict(test[X.columns])]
""", sensors_csv, ["naive bayes", "svm"]),
    Spec(
        "13", "Exp 13 · Confusion matrix and metrics",
        "Experiment 13: write `evaluate(y_true, y_pred)` for a **binary** problem (positive class = 1). "
        "**Return** (do not print) a dictionary:\n\n"
        "```\n{\"confusion_matrix\": [[TN, FP], [FN, TP]], \"accuracy\": ..., \"precision\": ..., "
        "\"recall\": ..., \"f1\": ...}\n```\n\nNumbers are compared to 6 decimal places. You may use "
        "`sklearn.metrics`; convert NumPy numbers with `int()` / `float()`.",
        "function", {"function_name": "evaluate", "params": ["y_true", "y_pred"], "compare": "float",
                     "tolerance": 1e-6},
        """from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_score, recall_score

def evaluate(y_true, y_pred):
    matrix = confusion_matrix(y_true, y_pred, labels=[0, 1])
    return {
        "confusion_matrix": [[int(v) for v in row] for row in matrix],
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
    }
""",
        "medium", ["metrics", "confusion matrix"],
        tests=[(json.dumps([[1, 0, 1, 1, 0, 1, 0, 0], [1, 0, 0, 1, 0, 1, 1, 0]]), True),
               (json.dumps(labels(41, 120, 0.2)), False), (json.dumps(labels(42, 300, 0.35)), False)],
    ),
    Spec(
        "14", "Exp 14 · Cross-validation",
        "Experiment 14: write `cross_validate_model(csv_text, k)`. The CSV has features `x1, x2, x3` and a 0/1 "
        "`label`.\n\nUse `LogisticRegression(max_iter=1000)` on the raw features and `KFold(n_splits=k)` "
        "(**no shuffling**). **Return** `{\"fold_scores\": [accuracy of each fold], \"mean\": mean accuracy}`.\n\n"
        "Then, in your write-up, compare the training and validation scores to discuss bias and variance.",
        "function", {"function_name": "cross_validate_model", "params": ["csv_text", "k"], "compare": "float",
                     "tolerance": 1e-3},
        """import io
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import KFold, cross_val_score

def cross_validate_model(csv_text, k):
    data = pd.read_csv(io.StringIO(csv_text))
    X, y = data.drop(columns="label"), data["label"]
    scores = cross_val_score(LogisticRegression(max_iter=1000), X, y, cv=KFold(n_splits=k))
    return {"fold_scores": [float(s) for s in scores], "mean": float(scores.mean())}
""",
        "hard", ["cross-validation", "bias-variance"],
        tests=[(json.dumps([cv_csv(51, 60), 3]), True), (json.dumps([cv_csv(52, 200), 5]), False),
               (json.dumps([cv_csv(53, 300), 10]), False)],
    ),
    _ml("15", "Exp 15 · Regularization: Ridge, Lasso and Elastic Net",
        "Experiment 15: the data has **20 features**, only a few of which matter, and few rows, so plain linear "
        "regression over-fits. Use **Ridge**, **Lasso** or **Elastic Net** and tune `alpha` with "
        "cross-validation (e.g. `GridSearchCV`) to predict `progression`.\n\nScored by **R²**: full marks at "
        "**0.85**, half marks at **0.70**.",
        {"task": "regression", "target": "progression", "metric": "r2",
         "thresholds": [{"at": 0.85, "credit": 1.0}, {"at": 0.7, "credit": 0.5}]},
        """import io
import pandas as pd
from sklearn.linear_model import ElasticNet, Lasso, Ridge
from sklearn.model_selection import GridSearchCV

def solve(train_csv, test_csv):
    train = pd.read_csv(io.StringIO(train_csv))
    test = pd.read_csv(io.StringIO(test_csv))
    X, y = train.drop(columns="progression"), train["progression"]
    best = None
    for model, grid in ((Ridge(), {"alpha": [0.1, 1, 10, 30]}),
                        (Lasso(max_iter=10000), {"alpha": [0.01, 0.1, 0.5, 1]}),
                        (ElasticNet(max_iter=10000), {"alpha": [0.01, 0.1, 0.5], "l1_ratio": [0.2, 0.5, 0.8]})):
        search = GridSearchCV(model, grid, cv=5, scoring="r2").fit(X, y)
        print(type(model).__name__, search.best_params_, round(search.best_score_, 3))
        if best is None or search.best_score_ > best.best_score_:
            best = search
    return [float(p) for p in best.predict(test[X.columns])]
""", diabetes_like_csv, ["regularization", "ridge", "lasso", "elastic net", "hyperparameter tuning"],
        difficulty="hard", n_train=70, n_test=60),
]


# --------------------------------------------------------------------- build it


class Builder:
    def __init__(self, url: str) -> None:
        import httpx  # only needed to build the lab, not for the datasets above

        self.http = httpx.Client(base_url=url.rstrip("/"), timeout=180)
        r = self.http.post("/api/v1/auth/login", json={"email": PROFESSOR, "password": PASSWORD, "client": "portal"})
        if r.status_code != 200:
            raise SystemExit(f"Could not sign in as {PROFESSOR}: {r.text[:200]} (run `python -m app.cli demo-data`)")
        self.h = {"Authorization": f"Bearer {r.json()['token']}"}

    def call(self, method: str, path: str, expect: int = 200, **kwargs) -> Any:
        r = self.http.request(method, path, headers=self.h, **kwargs)
        if r.status_code != expect:
            raise SystemExit(f"{method} {path} -> {r.status_code}: {r.text[:400]}")
        return r.json() if r.content else None

    def run(self) -> int:
        graders = {g["key"] for g in self.call("GET", "/api/v1/graders")}
        if "plot" not in graders:
            raise SystemExit("This server has no 'plot' grader yet. Restart the backend with the latest code.")
        subjects = {s["code"]: s for s in self.call("GET", "/api/v1/subjects")}
        if SUBJECT_CODE in subjects:
            print(f"Subject {SUBJECT_CODE} already exists; nothing added.")
            return 0
        batches = {b["code"]: b["id"] for b in self.call("GET", "/api/v1/catalog/batches")}
        subject = self.call("POST", "/api/v1/subjects", expect=201, json={
            "code": SUBJECT_CODE, "name": "Basics of Machine Learning Lab (BML)",
            "language_keys": ["python3-ml"], "batch_ids": [batches[BATCH_CODE]] if BATCH_CODE in batches else []})
        print(f"Subject {SUBJECT_CODE} created ({subject['id']})")
        failures = 0
        question_ids: dict[str, str] = {}
        for spec in SPECS:
            q = self.call("POST", f"/api/v1/subjects/{subject['id']}/questions", expect=201, json={
                "title": spec.title, "statement_md": spec.statement, "difficulty": spec.difficulty, "tags": spec.tags,
                "grader": spec.grader, "grader_config": spec.config, "marks": spec.marks,
                "time_limit_ms": TIME_LIMIT_MS, "memory_limit_kb": MEMORY_KB,
                "reference_language": "python3-ml", "reference_code": spec.reference,
                "starter_code": {"python3-ml": spec.starter} if spec.starter else {}})
            qid = q["id"]
            question_ids[spec.practical] = qid
            if spec.grader == "ml":
                for train, test, sample in spec.datasets:
                    self.call("POST", f"/api/v1/questions/{qid}/datasets", expect=201,
                              files={"train": ("train.csv", train, "text/csv"), "test": ("test.csv", test, "text/csv")},
                              data={"is_sample": str(sample).lower()})
            else:
                self.call("POST", f"/api/v1/questions/{qid}/tests", expect=201,
                          json={"tests": [{"input": text, "is_sample": sample} for text, sample in spec.tests]})
                if spec.grader in ("stdio", "function"):
                    report = self.call("POST", f"/api/v1/questions/{qid}/generate-outputs")
                    if not report.get("ok"):
                        print(f"  [FAIL] Exp {spec.practical}: generate outputs: {json.dumps(report)[:300]}")
                        failures += 1
                        continue
            check = self.call("POST", f"/api/v1/questions/{qid}/validate")
            ok = check["fraction"] == 1.0
            failures += not ok
            detail = "" if ok else " " + "; ".join(f"{t['verdict']} {t.get('message') or t.get('stderr', '')[:200]}"
                                                    for t in check["tests"])
            print(f"  [{'OK' if ok else 'FAIL'}] Exp {spec.practical}: {spec.title} ({spec.grader}){detail}")

        exam = self.call("POST", f"/api/v1/subjects/{subject['id']}/exams", expect=201, json={
            "title": "BML practical (demo): regression & classification", "duration_minutes": 120,
            "question_ids": [question_ids[p] for p in ("7", "8", "9", "10", "11", "12")],
            "selection": {"mode": "random", "rules": [{"difficulty": "medium", "count": 1},
                                                      {"difficulty": "hard", "count": 1}]},
            "languages": ["python3-ml"], "marking": {"auto_percent": 80}})
        print(f"Draft exam created: {exam['title']} ({exam['id']}): 1 medium + 1 hard question per student, "
              "80% automatic / 20% manual (for the write-up)")
        print("ALL BML QUESTIONS VALIDATED" if failures == 0 else f"{failures} QUESTION(S) FAILED VALIDATION")
        return 0 if failures == 0 else 1


def run(url: str) -> int:
    return Builder(url).run()
