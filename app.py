import streamlit as st
import pandas as pd
from pathlib import Path

from road_energy_review.core import (
    APP_VERSION, init_db, db, records, search_openalex, search_crossref,
    import_file_bytes, decide, set_category, set_full_text, latest_decision,
    search_logs, export_excel, export_word, prisma_summary
)

st.set_page_config(page_title=f"Road Energy Review v{APP_VERSION}", layout="wide")
init_db()
st.title(f"Road Energy Review v{APP_VERSION}")
st.caption("Систематический обзор дорожных энергетических установок · локальная SQLite · миграция v1.0 поддерживается")

tabs = st.tabs(["Поиск и импорт", "Реестр", "Скрининг", "Полные тексты", "Патенты и диссертации", "Журнал поиска", "PRISMA", "Экспорт"])

with tabs[0]:
    st.subheader("OpenAlex API")
    with st.form("openalex_search_v11"):
        query = st.text_area("Запрос", '"road energy harvesting" OR "piezoelectric pavement"')
        c1, c2, c3 = st.columns(3)
        start = c1.text_input("С даты", "2010-01-01")
        end = c2.text_input("По дату", "")
        limit = c3.number_input("Максимум записей", 1, 2000, 100)
        if st.form_submit_button("Искать в OpenAlex"):
            try:
                with st.spinner("Выполняется поиск..."):
                    found, inserted, merged = search_openalex(query, start, end or None, int(limit))
                st.success(f"Получено: {found} · новых: {inserted} · объединено/обогащено: {merged}")
            except Exception as exc:
                st.error(f"Ошибка OpenAlex: {exc}")

    st.divider()
    st.subheader("Crossref API")
    with st.form("crossref_search_v11"):
        query2 = st.text_input("Библиографический запрос", '"road energy harvesting"')
        d1, d2, d3 = st.columns(3)
        start2 = d1.text_input("С даты", "2010-01-01", key="crossref_start")
        end2 = d2.text_input("По дату", "", key="crossref_end")
        limit2 = d3.number_input("Максимум записей", 1, 2000, 100, key="crossref_limit")
        if st.form_submit_button("Искать в Crossref"):
            try:
                with st.spinner("Выполняется поиск..."):
                    found, inserted, merged = search_crossref(query2, start2, end2 or None, int(limit2))
                st.success(f"Получено: {found} · новых: {inserted} · объединено/обогащено: {merged}")
            except Exception as exc:
                st.error(f"Ошибка Crossref: {exc}")

    st.divider()
    st.subheader("Импорт библиографической выгрузки")
    uploaded = st.file_uploader("CSV, XLSX, RIS или BibTeX", type=["csv", "xlsx", "xls", "ris", "bib", "bibtex"])
    source_name = st.text_input("Название источника", "Scopus / WoS / eLibrary / CyberLeninka")
    if uploaded and st.button("Импортировать файл", key="import_v11"):
        try:
            total, inserted, merged = import_file_bytes(uploaded.name, uploaded.getvalue(), source_name)
            st.success(f"Строк в файле: {total} · новых записей: {inserted} · объединено/обогащено: {merged}")
        except Exception as exc:
            st.error(f"Ошибка импорта: {exc}")

with tabs[1]:
    st.subheader("Библиографический реестр")
    f1, f2 = st.columns(2)
    kind_filter = f1.selectbox("Тип документа", ["Все", "publication", "patent", "dissertation"])
    status_filter = f2.selectbox("Статус скрининга", ["Все", "pending", "maybe", "include", "exclude"])
    rows = records(None if kind_filter == "Все" else kind_filter,
                   None if status_filter == "Все" else status_filter)
    st.metric("Записей по фильтру", len(rows))
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("По выбранным фильтрам записей нет.")

with tabs[2]:
    st.subheader("Скрининг публикаций")
    status_choice = st.selectbox("Показывать статус", ["pending", "maybe", "include", "exclude", "Все"], key="screen_filter_v11")
    screen_rows = records(status=None if status_choice == "Все" else status_choice)
    if not screen_rows:
        st.info("Нет записей с выбранным статусом.")
    else:
        labels = {f"{r['id']} — {r['title'][:120]}": r for r in screen_rows}
        selected_label = st.selectbox("Документ", list(labels), key="screen_record_selector_v11")
        record = labels[selected_label]
        st.write("**Авторы:**", record.get("authors") or "Не указаны")
        st.write("**Год / дата / DOI:**", record.get("year") or "Не установлен",
                 record.get("publication_date") or "", record.get("doi") or "DOI отсутствует")
        st.write("**Аннотация:**", record.get("abstract") or "Не загружена")
        st.write("**Предварительная подсказка T1–T5:**", record.get("suggested_category") or "Нет")
        st.caption("Подсказка алгоритма не является экспертной классификацией.")
        current = record.get("screening_status") or "pending"
        current_reason = record.get("exclusion_reason") or ""
        current_notes = record.get("reviewer_notes") or ""
        current_stage = record.get("screening_stage") or "title_abstract"
        reasons = [
            "", "Не относится к дорожным энергетическим установкам",
            "Рекуперация только внутри автомобиля", "Нет генерации электрической энергии",
            "Нет связи с транспортным потоком или дорожной инфраструктурой",
            "Полный текст недоступен", "Недостаточно технической информации",
            "Дубликат", "Дата публикации вне периода обзора", "Другое"
        ]
        with st.form(f"screen_form_record_{record['id']}"):
            stage = st.radio("Этап отбора", ["title_abstract", "full_text"],
                             index=0 if current_stage == "title_abstract" else 1,
                             horizontal=True, key=f"stage_{record['id']}")
            decision = st.radio("Решение", ["pending", "maybe", "include", "exclude"],
                                index=["pending", "maybe", "include", "exclude"].index(current)
                                if current in ["pending", "maybe", "include", "exclude"] else 0,
                                horizontal=True, key=f"decision_{record['id']}")
            reason = st.selectbox("Причина исключения", reasons,
                                  index=reasons.index(current_reason) if current_reason in reasons else 0,
                                  key=f"reason_{record['id']}")
            notes = st.text_area("Комментарий эксперта", value=current_notes, key=f"notes_{record['id']}")
            reviewer = st.text_input("Эксперт", key=f"reviewer_{record['id']}")
            cat_options = ["", "T1", "T2", "T3", "T4", "T5"]
            existing_cat = record.get("category") or ""
            cat = st.selectbox("Подтверждённая категория", cat_options,
                               index=cat_options.index(existing_cat) if existing_cat in cat_options else 0,
                               key=f"category_{record['id']}")
            if st.form_submit_button("Сохранить решение"):
                try:
                    decide(record["id"], decision, reason, notes, reviewer, stage)
                    set_category(record["id"], cat)
                    st.success(f"Решение сохранено для записи ID {record['id']}.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
        history = None
        with db() as con:
            history = pd.read_sql_query(
                "SELECT id,decision,reason,notes,reviewer,stage,decided_at FROM decisions WHERE record_id=? ORDER BY id DESC",
                con, params=(record["id"],)
            )
        if not history.empty:
            with st.expander("История решений по этой публикации"):
                st.dataframe(history, use_container_width=True, hide_index=True)

with tabs[3]:
    st.subheader("Проверка доступности полного текста")
    full_rows = records()
    if not full_rows:
        st.info("Реестр пока пуст.")
    else:
        opts = {f"{r['id']} — {r['title'][:120]}": r for r in full_rows}
        selected = st.selectbox("Публикация", list(opts), key="fulltext_record_v11")
        rec = opts[selected]
        st.write("Текущий статус:", rec.get("full_text_available") or "unknown")
        with st.form(f"fulltext_form_{rec['id']}"):
            availability = st.radio("Полный текст доступен?", ["unknown", "yes", "no"],
                                    index=["unknown", "yes", "no"].index(rec.get("full_text_available") or "unknown"),
                                    horizontal=True, key=f"fulltext_status_{rec['id']}")
            full_url = st.text_input("Ссылка на полный текст", value=rec.get("full_text_url") or "",
                                     key=f"fulltext_url_{rec['id']}")
            if st.form_submit_button("Сохранить проверку"):
                set_full_text(rec["id"], availability, full_url)
                st.success("Результат проверки полного текста сохранён.")
                st.rerun()
    st.caption("Статус unknown означает, что доступность ещё не проверялась. Отсутствие аннотации не равнозначно отсутствию полного текста.")

with tabs[4]:
    for typ, label in (("patent", "Патенты"), ("dissertation", "Диссертации")):
        st.subheader(label)
        data = records(typ)
        if data:
            st.dataframe(pd.DataFrame(data), use_container_width=True, hide_index=True)
        else:
            st.info("Пока нет записей.")

with tabs[5]:
    st.subheader("Журнал поисковых сессий и импортов")
    logs = search_logs()
    if logs:
        st.dataframe(pd.DataFrame(logs), use_container_width=True, hide_index=True)
    else:
        st.info("Журнал пока пуст.")

with tabs[6]:
    st.subheader("Сводка для контроля PRISMA")
    summary = prisma_summary()
    c1, c2, c3 = st.columns(3)
    c1.metric("Уникальных записей в реестре", summary["unique_records"])
    c2.metric("Сумма найденного по сессиям", summary["raw_retrieved_sum"])
    c3.metric("Сессий поиска/импорта", summary["search_sessions"])
    st.warning("Сумма результатов поисковых сессий может включать повторные запросы. Это контрольные показатели, а не окончательные числа PRISMA.")
    st.subheader("Статусы скрининга")
    st.dataframe(pd.DataFrame([{"Статус": k, "Количество": v} for k, v in summary["statuses"].items()]),
                 use_container_width=True, hide_index=True)
    st.subheader("Типы документов")
    st.dataframe(pd.DataFrame([{"Тип": k, "Количество": v} for k, v in summary["document_types"].items()]),
                 use_container_width=True, hide_index=True)

with tabs[7]:
    st.subheader("Экспорт данных")
    if st.button("Сформировать Excel", key="make_excel_v11"):
        try:
            output = export_excel()
            st.session_state["excel_bytes_v11"] = output.read_bytes()
            st.success("Excel сформирован.")
        except Exception as exc:
            st.error(f"Ошибка Excel: {exc}")
    if "excel_bytes_v11" in st.session_state:
        st.download_button("Скачать Excel", st.session_state["excel_bytes_v11"],
                           file_name="road_energy_review_v1.1.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.divider()
    if st.button("Сформировать Word", key="make_word_v11"):
        try:
            output = export_word()
            st.session_state["word_bytes_v11"] = output.read_bytes()
            st.success("Word сформирован.")
        except Exception as exc:
            st.error(f"Ошибка Word: {exc}")
    if "word_bytes_v11" in st.session_state:
        st.download_button("Скачать Word", st.session_state["word_bytes_v11"],
                           file_name="road_energy_review_report_v1.1.docx",
                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
