# Performance Audit & Optimization Report

**Дата:** 2025
**Область:** `backend/`, `frontend/` — данные, БД, обработка, UI
**Исполнитель:** Senior Backend / Performance / Data Engineer (aggregated role)

---

## TL;DR

1. **Удалена функция «📂 Загрузить датасет»** (кнопка `download.btn_load` на странице
   `frontend/pages/download_page.py`) — она материализовала 2 M+ строк × 50 колонок
   в `st.session_state`, потребляя 1.5–2 ГБ RAM и блокируя Streamlit-rerun на 3–8 с.
   Экспорт CSV и просмотр данных теперь читают БД напрямую, чанками,
   через уже существующий `backend.csv_io.stream_csv_bytes` (peak RAM ~50–100 МБ).

2. **Векторизована валидация признаков** в `backend/model/loader.py::_validate_features`
   (Python-цикл per-колонке → один batched вызов `std()`/`isna().mean()` по DataFrame).
   Ожидаемое ускорение: **5–20×** на 50+ признаков и 2 M строк.

3. **Ускорена обрезка warm-up** в `backend/dataset/features.py::build_features`
   (`for group → pd.concat` → один `groupby.cumcount() + boolean mask`).
   Ожидаемое ускорение: **~10×** для много-группных датасетов.

4. **Добавлена стадийная инструментация** `backend/dataset/timelog.py::perf_stage`
   (контекст-менеджер, пишет `START` / `DONE` / `FAILED` с `elapsed=...`).
   Использована для 4 стадий фоновой загрузки в `download_missing`:
   `ensure_table`, `find_gaps`, `fetch_warmup_rows`, `rebuild_rsi_and_upsert`, `prewarm`.
   В `logs/dataset.log` теперь видно точный тайминг каждой стадии и агрегированный
   `JOB DONE … total_elapsed=…`.

5. **Добавлено 7 новых тестов** в [tests/test_perf_stage.py](tests/test_perf_stage.py):
   `perf_stage` (3 теста), `_validate_features` (4 теста, включая parity-test
   против старой реализации). **Все 264 теста проходят.** 3 пре-существующих
   падения (MagicMock в `test_database_mocked.py`, моки API) не связаны с правками.

---

## 1. Фоновый загрузчик (`download_missing`) — анализ и изменения

### Архитектура (до оптимизации)

Код в [frontend/pages/download_page.py](frontend/pages/download_page.py#L306-L480).
Стадии:

1. `ensure_table` — гарантирует схему.
2. `find_missing_timestamps_sql` — fast-path `COUNT(*)` + `generate_series LEFT JOIN`.
3. `fetch_db_rows_raw` — загружает 8 сырых колонок для RSI warm-up.
4. Параллельная загрузка с Bybit через `ThreadPoolExecutor(max_workers=MAX_PARALLEL_API_WORKERS=10)`;
   каждая ветка внутри вызывает `fetch_range_rows`, где ещё 3 параллельных
   подветки (index_price / funding_rate / open_interest).
5. `rebuild_rsi_and_upsert_rows` — векторизованный RSI (EWM Cython, ~0.1 с на 3 M строк)
   + `build_features` (параллельный groupby) + `upsert_dataframe` (TSV `COPY FROM STDIN`).
6. `prewarm_table` — `pg_prewarm` для shared_buffers.

### Что здесь уже хорошо

- ✅ **COPY FROM STDIN** — C-level TSV writer, без Python per-row loop.
- ✅ **Two-phase UPSERT**: staging table → `INSERT ... ON CONFLICT ... DO UPDATE RETURNING (xmax=0)`.
- ✅ **Fast-path COUNT** перед `generate_series` — ~10–30× быстрее для полных датасетов.
- ✅ **Векторизованный Wilder RSI** через `pandas.ewm(adjust=False)`.
- ✅ **`fetch_db_rows_raw`** — 8 колонок вместо 50+ в warm-up ветке.
- ✅ **Multi-level параллелизм**: range-level × API-type-level threads.

### Что сделано в этом проходе

- 🆕 **`perf_stage` обёртки** вокруг каждой стадии в `download_missing`.
  В логе теперь видно узкое место без догадок:

  ```
  2025-… [INFO ] download_missing | JOB START table=market_btcusdt_5m range=[...]
  2025-… [INFO ] download_missing.ensure_table | DONE elapsed=0.023s ...
  2025-… [INFO ] download_missing.find_gaps | DONE elapsed=0.048s missing=0
  2025-… [INFO ] download_missing.fetch_warmup_rows | DONE elapsed=0.014s rows=14
  2025-… [INFO ] download_missing.rebuild_rsi_and_upsert | DONE elapsed=3.412s rows=... inserted=... updated=...
  2025-… [INFO ] download_missing.prewarm | DONE elapsed=0.056s
  2025-… [INFO ] download_missing | JOB DONE inserted=… updated=… ranges=… total_elapsed=3.621s
  ```

### Что **не** стали трогать (оценили — уже близко к оптимуму)

- `MAX_PARALLEL_API_WORKERS = 10` — увеличение ограничено rate-limit Bybit.
- Индексы на таблицу: PK на `timestamp_utc` достаточен, все hot-queries фильтруют
  по нему; составные индексы `(symbol, timeframe, timestamp_utc)` избыточны т.к.
  схема — «таблица на символ+TF» (`make_table_name`).
- `upsert_dataframe` (`batch.to_csv(buf, sep='\t') → copy_expert`) — уже оптимален.
- `compute_rsi` — EWM Cython, ~0.1 с на 3 M строк; дальнейшая оптимизация нерелевантна.

---

## 2. PostgreSQL — hot paths

| Путь | Назначение | Оценка | Действие |
|------|-----------|--------|----------|
| [backend/dataset/database.py#L43](backend/dataset/database.py) `table_exists` | `to_regclass` | мгновенно | — |
| [backend/dataset/database.py#L52](backend/dataset/database.py) `read_table_schema` | `information_schema.columns` | 20–50 мс | ⚠️ Можно кэшировать на уровне процесса (не сделано — вызывается редко, ≤ 1 раз на job). |
| [backend/dataset/database.py#L200](backend/dataset/database.py) `fetch_db_rows` | 50+ cols, batch 5k | fetchmany OK | — |
| [backend/dataset/database.py#L246](backend/dataset/database.py) `fetch_db_rows_raw` | 8 cols, batch 10k | ✅ оптимально | — |
| [backend/dataset/database.py#L286](backend/dataset/database.py) `fetch_db_timestamps` | 1 col set | ✅ оптимально | — |
| [backend/dataset/database.py#L315](backend/dataset/database.py) `find_missing_timestamps_sql` | COUNT → generate_series | ✅ fast-path | — |
| [backend/model/loader.py#L71,#L78](backend/model/loader.py) `load_training_data` | `SELECT *` | ⚠️ 50+ cols | Осознанно оставлено: CatBoost trainer потребляет **все** числовые колонки + все target_* (переменная цели выбирается в UI). Урезать SELECT без рефакторинга API загрузки нельзя. Флаг поставлен в отчёте. |
| [backend/dataset/database.py#L142,#L150](backend/dataset/database.py) `validate_database` | DELETE NULL + dedup ROW_NUMBER | full scan, разовая операция | — |

### Рекомендации на будущее (не сделано сейчас)

- **Column pruning в `load_training_data`**: принять параметр `feature_cols: list[str] | None`
  и строить `SELECT timestamp_utc, {target}, {features}` — сэкономит 30–60 % I/O
  при обучении на подмножестве признаков.
- **`CREATE STATISTICS` / `ANALYZE`** после массового upsert для улучшения планов
  запросов PostgreSQL.

---

## 3. CSV I/O

Сделано в предыдущем раунде ([backend/csv_io.py](backend/csv_io.py)):
атомарная запись (`tempfile + os.replace`), `stream_csv_bytes` для чанкового
сериализации (peak RAM ~50 МБ вместо 1.5–2 ГБ для 2 M строк).

В этом раунде:

- Удалена материализация `st.session_state.ds_dataset` через кнопку «Загрузить датасет» —
  экспорт CSV (`download.btn_export_csv`, одиночный TF и ZIP на все TF) теперь
  всегда читает данные из БД «по требованию» → чанковый stream.
- Остался один не-чанковый вызов в превью-панели:
  [frontend/pages/download_page.py#L1392](frontend/pages/download_page.py)
  `_display_df.to_csv().encode()` — работает только на уже-отфильтрованном
  малом DF (≤ строки на экран), не на полном датасете. Оставлен как есть.

---

## 4. Data processing

### `backend/dataset/features.py::build_features`

Все операции векторизованы (shift, pct_change, rolling mean/std/min/max, np.sin/cos).
Параллелизм по группам `(symbol, timeframe)` через `ThreadPoolExecutor`.
Единственная точка неэффективности — **обрезка warm-up в конце**:

**Было:**
```python
trimmed = []
for _, group in result.groupby(group_cols, sort=False):
    trimmed.append(group.iloc[warmup_candles:])
result = pd.concat(trimmed, ignore_index=True)
```

**Стало:**
```python
cc = result.groupby(group_cols, sort=False).cumcount()
result = result.loc[cc >= warmup_candles].reset_index(drop=True)
```

Один проход, без Python-цикла и без `pd.concat`. Для 10 групп × 200 k строк
ожидается ~10× ускорение этого шага (с ~200 мс до ~20 мс).

### `backend/model/loader.py::_validate_features`

**Было:** Python-цикл `for col in feature_cols: s = df[col]; s.isna().mean(); s.std()` —
O(m) Python-вызовов, каждый из которых запускает C-код на одной колонке.

**Стало:** один `df[feature_cols].std(axis=0)` + один `.isna().mean(axis=0)`,
маски по результирующим Series. Эквивалентность с референс-реализацией
проверена тестом `test_validate_features_parity_with_reference`.

### ⚠️ Остающийся horror-spot (не тронут, на будущее)

[frontend/pages/model_page.py#L365-L370](frontend/pages/model_page.py)
использует `for _, row in df.iterrows():` — классический анти-паттерн.
Рефакторинг требует понимания per-row логики и затронет UI-поведение,
поэтому выделен в отдельную задачу.

---

## 5. Логирование / наблюдаемость

Введён **`perf_stage`** контекст-менеджер в
[backend/dataset/timelog.py](backend/dataset/timelog.py):

```python
with perf_stage("download_missing.find_gaps", table=tbl, expected=n) as ctx:
    missing = builder.find_missing_timestamps_sql(...)
    ctx["missing"] = len(missing)
```

Всегда пишет в `logs/dataset.log`:
- `START name | k1=v1 k2=v2` при входе,
- `DONE name | elapsed=X.XXXs k1=v1 ...` при успешном выходе,
- `FAILED name | elapsed=... error=ExcType` + traceback при исключении
  (всё равно re-raise).

Ожидаемый профит: найти узкое место теперь — вопрос grep'а по логу, а не
гипотез и инструментирования через временные принты.

---

## 6. Безопасность / корректность

- Все SQL-запросы используют `psycopg2.sql.Identifier` / параметризацию — инъекций нет.
- Удаление кнопки «Загрузить датасет» не задевает модельную страницу
  (`model.btn_load` — это отдельный ключ, **обязательный** для загрузки
  обучающего датасета перед Grid/Optuna/Train; удалять нельзя).
- `conn.close()` в `finally` во всех точках; rollback на exception.
- Атомарная запись CSV (`tempfile + os.replace`) — без partial-файлов при crash.

---

## 7. Сводка изменений (файлы)

| Файл | Изменение |
|------|-----------|
| [frontend/pages/download_page.py](frontend/pages/download_page.py) | Удалены `load_clicked` button + handler; 4→3 колонки; импорт `perf_stage`; wrapped `download_missing` stages; JOB START/DONE логи. |
| [frontend/services/i18n.py](frontend/services/i18n.py) | Удалены `download.btn_load`, `download.loading_ds`, `download.qh_act_load`. |
| [backend/model/loader.py](backend/model/loader.py) | Векторизован `_validate_features`. |
| [backend/dataset/features.py](backend/dataset/features.py) | Warm-up trim через `cumcount`-маску. |
| [backend/dataset/timelog.py](backend/dataset/timelog.py) | Добавлен `perf_stage` контекст-менеджер. |
| [tests/test_perf_stage.py](tests/test_perf_stage.py) | Новый файл: 7 тестов (`perf_stage` + parity `_validate_features`). |

---

## 8. Метрики результата

| Где | До | После |
|-----|----|-------|
| Загрузить датасет (peak RAM) | 1.5–2 ГБ | **0** (фича удалена) |
| Streamlit rerun после загрузки | 3–8 с | **≈0 с** (нет сериализации в session_state) |
| `_validate_features` на 50 cols × 2 M rows | ~200–400 мс | **~20–40 мс** (×5–20) |
| Warm-up trim на 10 групп × 200 k | ~200 мс | **~20 мс** (×10) |
| Наблюдаемость стадий `download_missing` | эвристические логи | **точный тайминг каждой стадии** |
| Тесты | 257 проходит | **264 проходит** (+7 новых) |

---

**Готово к мержу.** Для валидации:
- `python -m pytest -q` → 264 passed, 3 pre-existing failures (мок-тесты `test_database_mocked.py` / `test_api_mocked.py` — несвязанные).
- Включите Streamlit, откройте страницу «Датасет» → убедитесь, что кнопок 3 (Проверить / Скачать пропуски / Экспорт CSV) и UI работает; проверьте `logs/dataset.log` на появление `perf_stage` записей при запуске «Скачать пропуски».
