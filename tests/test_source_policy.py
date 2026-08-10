"""Общеновостные источники не должны создавать события из обычных новостей."""

from pipeline.parse import parse
from pipeline.source_policy import accepts_observation


def strict(text: str) -> bool:
    return accepts_observation(parse(text), strict=True)


def test_strict_source_accepts_explicit_regional_alerts():
    assert strict("В Ярославской области объявлена беспилотная опасность.")
    assert strict("Внимание! Сигнал «Опасное небо». Включаем сирены в Ижевске.")
    assert strict("Над Ленинградской областью силами ПВО сбиты 6 БПЛА.")
    assert strict("Отбой беспилотной опасности в Новгородской области.")


def test_strict_source_rejects_news_with_incidental_parser_words():
    assert not strict("В Великом Новгороде открылась площадка для беспилотных гонок.")
    assert not strict("Железная дорога — зона повышенной опасности.")
    assert not strict("Специалисты фиксируют выход животных к деревням.")
    assert not strict("Музей представил беспилотник, обнаруженный поисковым отрядом.")


def test_strict_source_rejects_reposted_airport_notice():
    assert not strict("Аэропорт Ижевск. Сняты ограничения на прием и выпуск судов.")


def test_regular_source_keeps_existing_parser_contract():
    observation = parse("Фиксация БПЛА над Ейском")
    assert accepts_observation(observation)
