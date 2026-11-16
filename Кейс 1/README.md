# Case-1 — поиск нетипичного поведения заявок на закупку

Разбор журнала событий процесса закупок (process mining). Скрипт `analysis.py` читает выгрузку
событий, восстанавливает маршрут каждой заявки и выгружает отклонения с объяснениями.

Результат: 88 строк в `anomalies.csv` (80 заявок из 1 000, 9 типов отклонений).
Методика, критерии, результаты и ограничения — в [`report.md`](report.md).

| Файл | Что это |
|---|---|
| `analysis.py` | Скрипт анализа: загрузка, длительности, поиск аномалий, выгрузка CSV |
| `anomalies.csv` | Результат: `ProcessInstanceId,AnomalyType,Explanation` |
| `report.md` | Отчёт с обоснованием критериев и разбором результатов |
| `README.md` | Запуск и формат данных |

## Запуск

```bash
pip install pandas numpy
python3 analysis.py students.csv anomalies.csv
```

Python 3.8+, зависимости — только `pandas` и `numpy`. Аргументы опциональны (по умолчанию
`students.csv` → `anomalies.csv`). Скрипт печатает сводку: число строк, уникальных процессов и
распределение по типам. Входной `students.csv` в репозиторий не входит.

## Входные данные

CSV, UTF-8 с BOM, разделитель — запятая, одна строка = одно событие. Колонки:
`ProcessInstanceId`, `EventId`, `EventIndex`, `EventType` (`process_started`, `task_created`,
`task_completed`, `process_completed`), `Timestamp`, `ActivityId`, `ActivityName`, `TaskId`,
`UserId`, `Role`, `Result` (`accept` / `return` / `reject` / `fulfilled` / `rejected`),
`RequestType` (`standard` / `contract` / `urgent`), `Amount`.

Ожидается: `EventIndex` идёт 1…n без пропусков, время не убывает с индексом, `TaskId` создания
и закрытия совпадают, `RequestType` и `Amount` постоянны внутри процесса. Часовой пояс и дата
среза заданы константами `TZ` и `CUTOFF` в начале скрипта.

## Этапы и легенда маршрута

`CHK` проверка → `MGR` руководитель → `BUD` бюджет → (`LEG` договор, только contract) → `EXE`
исполнение → `END`; `REW` — доработка, `AUD` — внеплановая проверка, `REG` — регистрация.
Маршрут кодируется строкой `CHK:a>MGR:a>BUD:a>EXE:a>END:f`, где суффикс — результат
(`a` accept, `ret` return, `rej` reject, `f` fulfilled, `r` rejected).

## Выход

`anomalies.csv` — UTF-8, кавычки для полей с запятыми, сортировка по `AnomalyType` и
`ProcessInstanceId`, дубли удалены. Одна заявка может дать несколько строк разных типов.

Критерии аномалий (структурные нарушения маршрута и количественные выбросы в рабочих часах)
описаны в разделе 3 отчёта; причины отклонений по данным неизвестны, поэтому файл — список
кандидатов на ручную проверку, а не доказанных нарушений.
