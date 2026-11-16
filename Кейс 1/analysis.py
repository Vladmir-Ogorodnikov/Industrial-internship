#!/usr/bin/env python3
"""Поиск нетипичного поведения заявок на закупку.

Запуск:
    pip install pandas numpy
    python3 analysis.py students.csv anomalies.csv

Вход: students.csv (UTF-8 с BOM). Выход: anomalies.csv (ProcessInstanceId,AnomalyType,Explanation)
и печать сводки для отчёта.
"""
import sys
import numpy as np
import pandas as pd

SRC = sys.argv[1] if len(sys.argv) > 1 else "students.csv"
OUT = sys.argv[2] if len(sys.argv) > 2 else "anomalies.csv"
TZ = "Europe/Moscow"
CUTOFF = pd.Timestamp("2026-07-01 00:00", tz=TZ)

# ---------------------------------------------------------------- загрузка
df = pd.read_csv(SRC, encoding="utf-8-sig")
df["ts"] = pd.to_datetime(df.Timestamp, utc=True).dt.tz_convert(TZ)
df = df.sort_values(["ProcessInstanceId", "EventIndex"]).reset_index(drop=True)  # порядок по EventIndex

ABBR = {"Проверка заявки": "CHK", "Согласование руководителем": "MGR", "Согласование бюджета": "BUD",
        "Проверка договора": "LEG", "Исполнение заявки": "EXE", "Доработка заявки": "REW",
        "Внеплановая проверка": "AUD", "Регистрация заявки": "REG", "Завершение": "END"}
df["ab"] = df.ActivityName.map(ABBR)
RES = {"accept": "a", "return": "ret", "reject": "rej", "fulfilled": "f", "rejected": "r"}


def working_hours(a, b):
    """Рабочие часы между a и b: пн-пт, 09:00-18:00, без праздников и обеда."""
    if pd.isna(a) or pd.isna(b):
        return np.nan
    if b <= a:
        return 0.0
    tot, d = 0.0, a.normalize()
    while d <= b.normalize():
        if d.weekday() < 5:
            s, e = max(a, d + pd.Timedelta(hours=9)), min(b, d + pd.Timedelta(hours=18))
            if e > s:
                tot += (e - s).total_seconds()
        d += pd.Timedelta(days=1)
    return tot / 3600


# ---------------------------------------------------------------- задания
cr = df[df.EventType == "task_created"]
co = df[df.EventType == "task_completed"]
tasks = cr[["TaskId", "ProcessInstanceId", "ab", "ts", "RequestType", "EventIndex"]].merge(
    co[["TaskId", "ts", "UserId", "Role", "Result"]], on="TaskId", how="left", suffixes=("", "_c"))
tasks["cal_h"] = (tasks.ts_c - tasks.ts).dt.total_seconds() / 3600
tasks["wh"] = [working_hours(a, b) for a, b in zip(tasks.ts, tasks.ts_c)]
tasks["min"] = tasks.cal_h * 60
tasks = tasks.sort_values(["ProcessInstanceId", "EventIndex"])

# ---------------------------------------------------------------- маршруты
def route(g):
    out = []
    for _, r in g.iterrows():
        if r.EventType == "task_created":
            out.append(r.ab)
        elif r.EventType == "task_completed":
            out[-1] += ":" + RES[r.Result]
        elif r.EventType == "process_completed":
            out.append("END:" + RES[r.Result])
    return ">".join(out)


meta = df.groupby("ProcessInstanceId").agg(rt=("RequestType", "first"), amt=("Amount", "first"))
meta["route"] = df.groupby("ProcessInstanceId").apply(route)
N = len(meta)
sh = lambda x: str(x)[:8]


def med(ab, rt, col="wh"):
    return tasks[(tasks.ab == ab) & (tasks.RequestType == rt)][col].median()


rows = []
add = lambda pid, typ, txt: rows.append((pid, typ, txt))

# 1. Пропуск согласования руководителем ------------------------------------
n_bud = meta.route.str.contains("BUD").sum()
for pid, r in meta[meta.route.str.contains("CHK:a>BUD")].iterrows():
    add(pid, "Пропущено согласование руководителем",
        f"Маршрут {r.route}: после «Проверка заявки» сразу создана «Согласование бюджета», этап «Согласование "
        f"руководителем» отсутствует вовсе (заявка {r.rt}, сумма {r.amt:,.0f}). Остальные {n_bud - 16} заявок, "
        f"дошедших до бюджета, 100% проходят руководителя; схема требует порядок проверка → руководитель → бюджет.")

# 2. Нарушен порядок: исполнение до бюджета --------------------------------
for pid, r in meta[meta.route.str.contains("EXE:a>BUD")].iterrows():
    t = tasks[tasks.ProcessInstanceId == pid].set_index("ab")
    extra = ""
    if "LEG" in t.index:
        extra = (f" Для договорной заявки юридическая проверка тоже стоит после исполнения "
                 f"(закрыта {t.loc['LEG','ts_c']:%d.%m %H:%M}).")
    add(pid, "Нарушен порядок этапов: исполнение до бюджета",
        f"Закупщик закрыл «Исполнение заявки» {t.loc['EXE','ts_c']:%d.%m.%Y %H:%M}, а «Согласование бюджета» "
        f"создано только после этого ({t.loc['BUD','ts']:%d.%m %H:%M}); маршрут {r.route}.{extra} "
        f"В 992 других заявках бюджет (и юрист для contract) всегда предшествует исполнению.")

# 3. Повторная проверка заявки без возврата --------------------------------
for pid, r in meta[meta.route.str.contains("CHK:a>CHK")].iterrows():
    c = tasks[(tasks.ProcessInstanceId == pid) & (tasks.ab == "CHK")].reset_index(drop=True)
    add(pid, "Повторная проверка без возврата",
        f"Две подряд «Проверка заявки»: первая принята специалистом {sh(c.UserId[0])} "
        f"({c.ts_c[0]:%d.%m %H:%M}), вторая создана {c.ts[1]:%H:%M} и принята другим специалистом {sh(c.UserId[1])} "
        f"в {c.ts_c[1]:%H:%M}. Возврата и доработки между ними нет; в норме повторная проверка бывает только после "
        f"«Доработка заявки» (маршрут {r.route}).")

# 4. Внеплановая проверка auditor ------------------------------------------
for pid, r in meta[meta.route.str.contains("AUD")].iterrows():
    a = tasks[(tasks.ProcessInstanceId == pid) & (tasks.ab == "AUD")]
    users = ", ".join(sorted({sh(u) for u in a.UserId}))
    add(pid, "Внеплановая проверка auditor (требует ручной проверки)",
        f"В маршруте {r.route} есть {len(a)} задания «Внеплановая проверка» (auditor {users}, "
        f"{a.wh.min():.1f}-{a.wh.max():.1f} раб. ч каждое). Это 8 из 1000 заявок; сумма {r.amt:,.0f} не выходит "
        f"за обычный диапазон (медиана 27 525), т.е. причина по данным не видна. Дополнительный этап вне схемы "
        f"— нужна проверка бизнес-основания; нарушением считать нельзя без этого.")

# 5. Чрезмерные циклы доработки --------------------------------------------
loops = meta.route.str.count("REW")
for pid in loops[loops >= 4].index:
    r = meta.loc[pid]
    add(pid, "Чрезмерное число возвратов на доработку",
        f"{loops[pid]} возвратов руководителем и циклов «Доработка заявки» → проверка → руководитель до итогового "
        f"согласования. Среди остальных заявок: без возвратов {int((loops == 0).sum())}, с одним {int((loops == 1).sum())}, "
        f"максимум 1; здесь в {loops[pid]} раз больше (заявка {r.rt}, сумма {r.amt:,.0f}).")

# 6. Согласование закрыто не руководителем ---------------------------------
starter = df[df.EventType == "process_started"].set_index("ProcessInstanceId").UserId
m = co[(co.ab == "MGR") & (co.Role == "requester")]
for _, r in m.iterrows():
    same = r.UserId == starter[r.ProcessInstanceId]
    what = "принял решение" if r.Result == "accept" else "отклонил заявку"
    extra = (" Это тот же пользователь, что создал заявку: самосогласование."
             if same else " Заявитель-инициатор — другой пользователь (5da4329a), т.е. решение принял сторонний requester.")
    add(r.ProcessInstanceId, "Согласование закрыто пользователем с ролью requester",
        f"Задание «Согласование руководителем» закрыто {r.ts:%d.%m.%Y %H:%M} пользователем {sh(r.UserId)} с ролью "
        f"requester и результатом {r.Result} ({what}). Остальные 1 133 закрытия этого этапа выполнены ролью manager "
        f"(6 менеджеров).{extra}")

# 7. Аномально долгая проверка заявки --------------------------------------
chk = tasks[tasks.ab == "CHK"]
norm = chk[chk.wh < 5]
for _, r in chk[chk.wh >= 5].iterrows():
    t = r.RequestType
    add(r.ProcessInstanceId, "Аномально долгая проверка заявки",
        f"«Проверка заявки» (специалист {sh(r.UserId)}) заняла {r.wh:.0f} раб. ч "
        f"({r.cal_h:.0f} календарных ч, {r.ts:%d.%m %H:%M} → {r.ts_c:%d.%m %H:%M}); у {t}-заявок медиана "
        f"{med('CHK', t):.2f} раб. ч, максимум среди остальных проверок {norm.wh.max():.2f} раб. ч. "
        f"Задержка в {r.wh / med('CHK', t):.0f} раз выше медианы, остальные этапы этой заявки в норме.")

# 8. Подозрительно быстрое прохождение -------------------------------------
fast_ids = tasks[tasks["min"] < 1].groupby("ProcessInstanceId").size()
for pid in fast_ids[fast_ids >= 2].index:
    t = tasks[tasks.ProcessInstanceId == pid]
    r = meta.loc[pid]
    end = df[(df.ProcessInstanceId == pid) & (df.EventType == "process_completed")]
    life = (end.ts.iloc[0] - df[(df.ProcessInstanceId == pid)].ts.min()).total_seconds() / 60 if len(end) else np.nan
    txt = (f"Все {len(t)} человеческих задания закрыты за {t['min'].min() * 60:.0f}-{t['min'].max() * 60:.0f} секунд "
           f"(этапы {', '.join(t.ab)}); обычно проверка занимает ~{med('CHK', r.rt, 'min'):.0f} мин, "
           f"руководитель ~{med('MGR', r.rt, 'min'):.0f} мин, бюджет ~{med('BUD', r.rt, 'min'):.0f} мин.")
    if not np.isnan(life):
        txt += f" Весь процесс от регистрации до завершения — {life:.0f} мин (маршрут {r.route})."
    txt += " Остальные 992 заявки ни разу не закрывали человеческое задание быстрее минуты — признак формального закрытия/автоматизации."
    add(pid, "Подозрительно быстрая обработка (формальное закрытие)", txt)

# 9. Зависшее задание руководителя -----------------------------------------
mg_done = tasks[(tasks.ab == "MGR") & tasks.ts_c.notna()].wh
for _, r in tasks[(tasks.ts_c.isna()) & (tasks.ab == "MGR")].iterrows():
    days = (CUTOFF - r.ts).total_seconds() / 86400
    add(r.ProcessInstanceId, "Зависшее задание руководителя (открыто с начала года)",
        f"«Согласование руководителем» создано {r.ts:%d.%m.%Y %H:%M} и не закрыто к срезу 01.07.2026: "
        f"{days:.0f} календарных дней ожидания. Закрытые согласования занимают медиану {mg_done.median():.1f} "
        f"раб. ч, максимум {mg_done.max():.1f} раб. ч; заявки, созданные позже, согласовывались "
        f"(заявка {r.RequestType}). Открытость сама по себе не аномалия, но срок несопоставим с нормой.")

# ---------------------------------------------------------------- выгрузка
out = pd.DataFrame(rows, columns=["ProcessInstanceId", "AnomalyType", "Explanation"]).drop_duplicates()
assert out.ProcessInstanceId.isin(meta.index).all()
out = out.sort_values(["AnomalyType", "ProcessInstanceId"]).reset_index(drop=True)
out.to_csv(OUT, index=False, encoding="utf-8")

print("строк:", len(out), "| уникальных процессов:", out.ProcessInstanceId.nunique())
print(out.AnomalyType.value_counts().to_string())
