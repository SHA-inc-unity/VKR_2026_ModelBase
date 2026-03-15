from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


FitModelFn = Callable[..., dict[str, Any]]
SignalThresholdsFn = Callable[[dict[str, float]], dict[str, float]]
DisplayFn = Callable[[Any], None]


ENTRY_POINT_C_HORIZON_MINUTES = 180
ENTRY_POINT_C_MOVE_PCT = 1.0


@dataclass(frozen=True)
class BacktestConfig:
    """Конфигурация упрощённого long-only backtest.

    Parameters:
        enabled: Включает или отключает выполнение блока backtest.
        start_days_ago: На сколько дней от конца истории сместить старт окна теста.
        duration_days: Длительность окна теста в днях.
        train_minutes: Размер обучающего окна перед каждой точкой backtest.
        retrain_every_hours: Частота переобучения модели в часах.
        force_cpu: Если True, запрещает использовать CUDA в backtest.
        save_to_csv: Сохранять ли журнал действий в CSV.
        display_max_trades: Сколько последних действий показывать в notebook.
        eval_every_minutes: Шаг прохода по истории в минутах.
        initial_capital: Начальный виртуальный капитал в USDT.
        entry_fraction: Доля свободного капитала на один вход.
        execution_fee_pct: Комиссия одной стороны сделки в процентах.
        min_60m_move_pct: Минимальный прогнозный рост на 60 минут для входа.
        cost_gate_ratio: Доля effective cost, которая участвует в фильтре входа.
        long_entry_score_min: Минимальный score для открытия long.
        exit_score_max: Верхний порог ослабления сигнала для выхода.
        cooldown_minutes: Пауза после закрытия позиции перед новым входом.
        min_hold_minutes: Минимальное время удержания позиции.
        target_exit_min_hold_minutes: Минимальное время до выхода по target.
        max_hold_minutes: Максимальное время удержания позиции.
        target_move_floor_pct: Нижняя граница целевого движения для фиксации прибыли.
        sharp_drop_5m_pct: Резкое падение на 5 минут для аварийного выхода.
        sharp_drop_10m_pct: Подтверждающее падение на 10 минут для аварийного выхода.
        live_confirm_1m_pct: Базовый подтверждающий порог движения.
        live_entry_min_move_pct: Минимальный порог движения для входа.
        live_stop_loss_pct: Процент стоп-лосса от цены входа.
        live_take_profit_pct: Процент тейк-профита от цены входа.
        live_forecast_horizon_minutes: Горизонт прогноза в минутах.
        live_signal_horizons: Горизонты, на которых строятся признаки сигнала.
        entry_point_c_horizon_minutes: Горизонт поиска точки входа C.
        entry_point_c_move_pct: Минимальный рост в процентах для триггера C.
    """

    enabled: bool
    start_days_ago: int
    duration_days: int
    train_minutes: int
    retrain_every_hours: int
    force_cpu: bool
    save_to_csv: bool
    display_max_trades: int
    eval_every_minutes: int
    initial_capital: float
    entry_fraction: float
    execution_fee_pct: float
    min_60m_move_pct: float
    cost_gate_ratio: float
    long_entry_score_min: float
    exit_score_max: float
    cooldown_minutes: int
    min_hold_minutes: int
    target_exit_min_hold_minutes: int
    max_hold_minutes: int
    target_move_floor_pct: float
    sharp_drop_5m_pct: float
    sharp_drop_10m_pct: float
    live_confirm_1m_pct: float
    live_entry_min_move_pct: float
    live_stop_loss_pct: float
    live_take_profit_pct: float
    live_forecast_horizon_minutes: int
    live_signal_horizons: tuple[int, ...]
    entry_point_c_horizon_minutes: int = ENTRY_POINT_C_HORIZON_MINUTES
    entry_point_c_move_pct: float = ENTRY_POINT_C_MOVE_PCT

    @property
    def step_minutes(self) -> int:
        """Возвращает безопасный шаг прохода по истории.

        Returns:
            Целое число не меньше 1.
        """

        return max(1, int(self.eval_every_minutes))

    @property
    def execution_fee_rate(self) -> float:
        """Возвращает комиссию в долях.

        Returns:
            Значение комиссии в формате коэффициента.
        """

        return float(self.execution_fee_pct / 100.0)


def normalize_backtest_history(history_df: pd.DataFrame) -> pd.DataFrame:
    """Подготавливает историю цен для backtest.

    Parameters:
        history_df: DataFrame с колонками timestamp и close.

    Returns:
        Очищенный DataFrame без пропусков, дублей и невалидных цен.
    """

    normalized = history_df[['timestamp', 'close']].copy()
    normalized['timestamp'] = pd.to_datetime(normalized['timestamp'], errors='coerce', utc=True)
    normalized['close'] = pd.to_numeric(normalized['close'], errors='coerce')
    normalized = normalized.dropna(subset=['timestamp', 'close'])
    normalized = normalized.loc[normalized['close'] > 0].copy()
    normalized = normalized.sort_values('timestamp').drop_duplicates(subset=['timestamp'], keep='last').reset_index(drop=True)
    return normalized


def validate_backtest_inputs(history_df: pd.DataFrame, config: BacktestConfig) -> None:
    """Проверяет, что истории и параметров достаточно для запуска.

    Parameters:
        history_df: Подготовленная история цен.
        config: Конфигурация backtest.

    Returns:
        Ничего не возвращает.
    """

    required_horizon_minutes = max(int(config.live_forecast_horizon_minutes), int(config.entry_point_c_horizon_minutes))
    minimum_rows = int(config.train_minutes + required_horizon_minutes)
    if len(history_df) <= minimum_rows:
        raise RuntimeError(
            f'Недостаточно истории для backtest: {len(history_df)} rows, нужно больше чем {minimum_rows}.'
        )

    if int(config.start_days_ago) <= 0:
        raise ValueError('BACKTEST_START_DAYS_AGO должен быть > 0.')
    if int(config.duration_days) <= 0:
        raise ValueError('BACKTEST_DURATION_DAYS должен быть > 0.')
    if int(config.retrain_every_hours) <= 0:
        raise ValueError('BACKTEST_RETRAIN_EVERY_HOURS должен быть > 0.')
    if float(config.initial_capital) <= 0.0:
        raise ValueError('BACKTEST_INITIAL_CAPITAL должен быть > 0.')
    if not (0.0 < float(config.entry_fraction) <= 1.0):
        raise ValueError('BACKTEST_ENTRY_FRACTION должен быть в диапазоне (0, 1].')
    if int(config.entry_point_c_horizon_minutes) <= 0:
        raise ValueError('ENTRY_POINT_C_HORIZON_MINUTES должен быть > 0.')
    if float(config.entry_point_c_move_pct) <= 0.0:
        raise ValueError('ENTRY_POINT_C_MOVE_PCT должен быть > 0.')


def clip_score(value: float, scale: float) -> float:
    """Нормализует движение цены в ограниченный диапазон score.

    Parameters:
        value: Исходное движение в процентах.
        scale: Масштаб нормализации.

    Returns:
        Значение score в диапазоне [-1.5, 1.5].
    """

    if not np.isfinite(value):
        return 0.0

    safe_scale = max(float(scale), 1e-6)
    return float(np.clip(float(value) / safe_scale, -1.5, 1.5))


def forecast_catboost_path(model_obj: dict[str, Any], history_series: pd.Series, horizon: int) -> pd.DataFrame:
    """Строит прогнозный путь цены на несколько минут вперёд.

    Parameters:
        model_obj: Словарь с моделью и параметрами нормализации.
        history_series: Исторический ряд цен закрытия.
        horizon: Горизонт прогноза в минутах.

    Returns:
        DataFrame с колонками step_minute и pred_price.
    """

    history_arr = pd.to_numeric(history_series, errors='coerce').dropna().astype(float).to_numpy()
    if len(history_arr) < 10:
        raise RuntimeError('Недостаточно истории для backtest-прогноза.')

    context = int(model_obj['context'])
    ret_mean = float(model_obj['ret_mean'])
    ret_std = float(model_obj['ret_std'])
    model = model_obj['model']

    history_log = list(np.log(np.clip(history_arr, 1e-8, None)).astype(float))
    history_ret_norm = list(((np.diff(np.log(np.clip(history_arr, 1e-8, None))) - ret_mean) / ret_std).astype(float))
    rows: list[dict[str, float]] = []

    for step_idx in range(int(horizon)):
        if len(history_ret_norm) >= context:
            x_ctx = np.asarray(history_ret_norm[-context:], dtype=np.float32)
        else:
            first_val = history_ret_norm[0] if len(history_ret_norm) > 0 else 0.0
            pad = np.full((context - len(history_ret_norm),), first_val, dtype=np.float32)
            x_ctx = np.concatenate([pad, np.asarray(history_ret_norm, dtype=np.float32)])

        pred_ret_norm = float(model.predict(x_ctx.reshape(1, -1))[0])
        pred_ret = float(np.clip(pred_ret_norm * ret_std + ret_mean, -0.20, 0.20))
        pred_log = float(history_log[-1] + pred_ret)
        pred_price = float(np.exp(pred_log))

        rows.append({'step_minute': int(step_idx + 1), 'pred_price': pred_price})
        history_log.append(pred_log)
        history_ret_norm.append(pred_ret_norm)

    return pd.DataFrame(rows)


def calc_move_pct(predicted_price: float, current_price: float) -> float:
    """Переводит прогноз цены в процент изменения.

    Parameters:
        predicted_price: Прогнозируемая цена.
        current_price: Текущая цена.

    Returns:
        Процент изменения относительно текущей цены.
    """

    if not np.isfinite(predicted_price) or not np.isfinite(current_price) or float(current_price) == 0.0:
        return float('nan')
    return float((float(predicted_price) / float(current_price) - 1.0) * 100.0)


def extract_horizon_forecasts(
    forecast_path_df: pd.DataFrame,
    current_price: float,
    horizons: tuple[int, ...],
) -> dict[str, float]:
    """Извлекает прогнозы и процентные движения на нужных горизонтах.

    Parameters:
        forecast_path_df: Полный путь прогноза по минутам.
        current_price: Текущая рыночная цена.
        horizons: Список интересующих горизонтов в минутах.

    Returns:
        Словарь с ключами вида pred_5m и move_5m_pct.
    """

    out: dict[str, float] = {}
    for horizon in horizons:
        horizon_idx = max(0, min(int(horizon) - 1, len(forecast_path_df) - 1))
        pred_price = float(forecast_path_df['pred_price'].iloc[horizon_idx])
        out[f'pred_{int(horizon)}m'] = pred_price
        out[f'move_{int(horizon)}m_pct'] = calc_move_pct(pred_price, current_price)
    return out


def build_entry_point_c_signal(
    forecast_path_df: pd.DataFrame,
    current_price: float,
    horizon_minutes: int,
    min_move_pct: float,
) -> dict[str, float]:
    """Строит точку входа C по 180-минутному прогнозу.

    Parameters:
        forecast_path_df: Полный минутный прогноз цены.
        current_price: Текущая рыночная цена.

    Returns:
        Словарь с максимумом роста и минутой, на которой он достигается.
    """

    scan_df = forecast_path_df.head(int(horizon_minutes)).copy()
    if len(scan_df) == 0:
        return {
            'entry_c_max_move_pct': float('nan'),
            'entry_c_max_step_minute': float('nan'),
            'entry_c_trigger': False,
        }

    scan_df['move_pct'] = scan_df['pred_price'].apply(lambda price: calc_move_pct(float(price), current_price))
    best_idx = scan_df['move_pct'].idxmax()
    best_row = scan_df.loc[best_idx]
    best_move_pct = float(best_row['move_pct'])

    return {
        'entry_c_max_move_pct': best_move_pct,
        'entry_c_max_step_minute': float(best_row['step_minute']),
        'entry_c_trigger': bool(np.isfinite(best_move_pct) and best_move_pct >= float(min_move_pct)),
    }


def build_trade_signal(
    current_price: float,
    forecast_path_df: pd.DataFrame,
    horizon_forecasts: dict[str, float],
    config: BacktestConfig,
    compute_signal_thresholds: SignalThresholdsFn,
) -> dict[str, Any]:
    """Формирует long-only торговый сигнал.

    Parameters:
        current_price: Текущая рыночная цена.
        forecast_path_df: Полный минутный прогноз цены.
        horizon_forecasts: Словарь прогнозов и движений по горизонтам.
        config: Конфигурация backtest.
        compute_signal_thresholds: Внешняя функция расчёта адаптивных порогов.

    Returns:
        Словарь с сигналом, score-метриками и флагами входа/выхода.
    """

    entry_c_horizon = int(config.entry_point_c_horizon_minutes)
    entry_c_move_pct = float(config.entry_point_c_move_pct)

    _ = compute_signal_thresholds

    move_1m_pct = float(horizon_forecasts.get('move_1m_pct', float('nan')))
    move_5m_pct = float(horizon_forecasts.get('move_5m_pct', float('nan')))
    move_10m_pct = float(horizon_forecasts.get('move_10m_pct', float('nan')))
    move_15m_pct = float(horizon_forecasts.get('move_15m_pct', float('nan')))
    move_30m_pct = float(horizon_forecasts.get('move_30m_pct', float('nan')))
    move_60m_pct = float(horizon_forecasts.get('move_60m_pct', float('nan')))

    entry_point_c = build_entry_point_c_signal(
        forecast_path_df=forecast_path_df,
        current_price=current_price,
        horizon_minutes=entry_c_horizon,
        min_move_pct=entry_c_move_pct,
    )
    entry_c_max_move_pct = float(entry_point_c['entry_c_max_move_pct'])
    entry_c_max_step_minute = float(entry_point_c['entry_c_max_step_minute'])
    entry_c_trigger = bool(entry_point_c['entry_c_trigger'])

    signal = 'hold'
    signal_type = 'none'
    entry_side = 'flat'
    entry_reason = f'wait: no entry point C detected on the {entry_c_horizon}-minute forecast'
    target_exit_price = float('nan')
    reason = f'entry point C not found: no minute in {entry_c_horizon}m forecast reached +{entry_c_move_pct:.2f}%'

    if entry_c_trigger:
        signal = 'long'
        signal_type = 'enter_long_C'
        entry_side = 'long'
        entry_reason = (
            f'enter long C: forecast reached +{entry_c_max_move_pct:.3f}% '
            f'on minute {int(entry_c_max_step_minute)} within {entry_c_horizon} minutes'
        )
        reason = entry_reason

    return {
        'signal': signal,
        'signal_type': signal_type,
        'entry_side': entry_side,
        'entry_reason': entry_reason,
        'reason': reason,
        'move_1m_pct': move_1m_pct,
        'move_5m_pct': move_5m_pct,
        'move_10m_pct': move_10m_pct,
        'move_15m_pct': move_15m_pct,
        'move_30m_pct': move_30m_pct,
        'move_60m_pct': move_60m_pct,
        'entry_c_max_move_pct': entry_c_max_move_pct,
        'entry_c_max_step_minute': entry_c_max_step_minute,
        'entry_c_trigger': entry_c_trigger,
        'entry_c_horizon_minutes': int(entry_c_horizon),
        'entry_c_required_move_pct': float(entry_c_move_pct),
        'long_open_trigger': bool(entry_side == 'long'),
        'long_close_sharp_trigger': False,
        'long_exit_trigger': False,
        'target_exit_price': target_exit_price,
    }


def position_levels(avg_entry_price: float, config: BacktestConfig) -> tuple[float, float]:
    """Считает уровни стоп-лосса и тейк-профита для long-позиции.

    Parameters:
        avg_entry_price: Цена входа.
        config: Конфигурация backtest.

    Returns:
        Кортеж из stop_loss и take_profit.
    """

    return (
        float(avg_entry_price * (1.0 - config.live_stop_loss_pct / 100.0)),
        float(avg_entry_price * (1.0 + config.live_take_profit_pct / 100.0)),
    )


def position_price_pnl(position: dict[str, Any] | None, current_price: float) -> float:
    """Считает PnL long-позиции только от движения цены.

    Parameters:
        position: Текущая позиция или None.
        current_price: Текущая цена рынка.

    Returns:
        Нереализованный PnL без комиссии.
    """

    if position is None:
        return 0.0

    qty = float(position['qty'])
    avg_entry = float(position['avg_entry_price'])
    return float((current_price - avg_entry) * qty)


def position_unrealized_pnl(position: dict[str, Any] | None, current_price: float, config: BacktestConfig) -> float:
    """Считает нереализованный PnL с учётом комиссии на гипотетический выход.

    Parameters:
        position: Текущая позиция или None.
        current_price: Текущая цена рынка.
        config: Конфигурация backtest.

    Returns:
        Нереализованный PnL после вычета exit fee.
    """

    if position is None:
        return 0.0

    exit_notional = float(position['qty'] * current_price)
    exit_fee = float(exit_notional * config.execution_fee_rate)
    return float(position_price_pnl(position, current_price) - exit_fee)


def open_position(
    signal_type: str,
    current_price: float,
    current_ts: pd.Timestamp,
    target_exit_price: float,
    free_cash: float,
    fraction: float,
    config: BacktestConfig,
) -> dict[str, Any] | None:
    """Открывает новую long-позицию.

    Parameters:
        signal_type: Тип сигнала, приведшего ко входу.
        current_price: Текущая цена входа.
        current_ts: Время открытия позиции.
        target_exit_price: Целевая цена выхода.
        free_cash: Свободный капитал.
        fraction: Доля капитала на вход.
        config: Конфигурация backtest.

    Returns:
        Словарь позиции или None, если открыть позицию нельзя.
    """

    allocation_cash = float(min(free_cash, max(0.0, free_cash * float(fraction))))
    if allocation_cash <= 0.0 or current_price <= 0.0:
        return None

    entry_fee_cash = float(allocation_cash * config.execution_fee_rate)
    exposure_cash = float(allocation_cash - entry_fee_cash)
    if exposure_cash <= 0.0:
        return None

    qty = float(exposure_cash / current_price)
    stop_loss, take_profit = position_levels(current_price, config)

    return {
        'side': 'long',
        'signal_type': signal_type,
        'opened_at': current_ts,
        'min_hold_until': current_ts + pd.Timedelta(minutes=config.min_hold_minutes),
        'target_exit_eligible_at': current_ts + pd.Timedelta(minutes=config.target_exit_min_hold_minutes),
        'force_exit_at': current_ts + pd.Timedelta(minutes=config.max_hold_minutes),
        'avg_entry_price': float(current_price),
        'qty': qty,
        'asset_qty': qty,
        'invested_cash_gross': allocation_cash,
        'entry_fee_cash': entry_fee_cash,
        'target_exit_price': float(target_exit_price),
        'stop_loss': float(stop_loss),
        'take_profit': float(take_profit),
    }


def close_position(
    position: dict[str, Any],
    current_price: float,
    current_ts: pd.Timestamp,
    reason: str,
    config: BacktestConfig,
) -> tuple[float, dict[str, Any]]:
    """Закрывает long-позицию.

    Parameters:
        position: Открытая позиция.
        current_price: Цена закрытия.
        current_ts: Время закрытия.
        reason: Причина закрытия.
        config: Конфигурация backtest.

    Returns:
        Кортеж из возвращённого кэша и словаря закрытой сделки.
    """

    exit_notional = float(position['qty'] * current_price)
    exit_fee_cash = float(exit_notional * config.execution_fee_rate)
    price_pnl = float(position_price_pnl(position, current_price))
    released_cash = float(position['invested_cash_gross'] + price_pnl - exit_fee_cash)
    realized_pnl = float(price_pnl - float(position['entry_fee_cash']) - exit_fee_cash)

    trade = {
        'side': 'long',
        'signal_type': position['signal_type'],
        'opened_at': position['opened_at'],
        'closed_at': current_ts,
        'avg_entry_price': float(position['avg_entry_price']),
        'exit_price': float(current_price),
        'target_exit_price': float(position['target_exit_price']),
        'qty': float(position['qty']),
        'asset_qty': float(position['qty']),
        'entry_fee_cash': float(position['entry_fee_cash']),
        'exit_fee_cash': float(exit_fee_cash),
        'invested_cash_gross': float(position['invested_cash_gross']),
        'gross_sale_value': float(exit_notional),
        'net_sale_value': float(released_cash),
        'realized_pnl': realized_pnl,
        'return_pct': float(realized_pnl / float(position['invested_cash_gross']) * 100.0) if float(position['invested_cash_gross']) > 0 else float('nan'),
        'close_reason': reason,
    }
    return released_cash, trade


def create_open_trade_action(
    retrain_hours: int,
    current_ts: pd.Timestamp,
    position: dict[str, Any],
    free_cash_after: float,
    realized_pnl_total: float,
    current_price: float,
    reason: str,
    config: BacktestConfig,
) -> dict[str, Any]:
    """Создаёт строку журнала для открытия long-позиции.

    Parameters:
        retrain_hours: Интервал переобучения сценария.
        current_ts: Время открытия.
        position: Открытая позиция после входа.
        free_cash_after: Свободный кэш после входа.
        realized_pnl_total: Накопленный реализованный PnL.
        current_price: Цена входа.
        reason: Причина входа.
        config: Конфигурация backtest.

    Returns:
        Словарь строки журнала с действием open_long.
    """

    equity_after = float(
        free_cash_after
        + float(position['invested_cash_gross'])
        + position_unrealized_pnl(position, current_price, config)
    )

    return {
        'retrain_hours': int(retrain_hours),
        'timestamp': current_ts,
        'action': 'open_long',
        'side': 'long',
        'signal_type': position['signal_type'],
        'price': float(current_price),
        'qty': float(position['qty']),
        'asset_qty': float(position['qty']),
        'cash_flow': float(-position['invested_cash_gross']),
        'invested_cash_gross': float(position['invested_cash_gross']),
        'entry_fee_cash': float(position['entry_fee_cash']),
        'exit_fee_cash': float('nan'),
        'gross_sale_value': float('nan'),
        'net_sale_value': float('nan'),
        'realized_pnl': float('nan'),
        'position_qty_after': float(position['qty']),
        'avg_entry_after': float(position['avg_entry_price']),
        'free_cash_after': float(free_cash_after),
        'equity_after': equity_after,
        'reason': reason,
        'grid_realized_pnl_after': float(realized_pnl_total),
    }


def create_close_trade_action(
    retrain_hours: int,
    current_ts: pd.Timestamp,
    position: dict[str, Any],
    closed_trade: dict[str, Any],
    released_cash: float,
    free_cash_after: float,
    reason: str,
) -> dict[str, Any]:
    """Создаёт строку журнала для закрытия long-позиции.

    Parameters:
        retrain_hours: Интервал переобучения сценария.
        current_ts: Время закрытия.
        position: Закрываемая позиция.
        closed_trade: Детали закрытой сделки.
        released_cash: Возвращённый на счёт кэш.
        free_cash_after: Свободный кэш после закрытия.
        reason: Причина закрытия.

    Returns:
        Словарь строки журнала с действием close_long.
    """

    return {
        'retrain_hours': int(retrain_hours),
        'timestamp': current_ts,
        'action': 'close_long',
        'side': 'long',
        'signal_type': position['signal_type'],
        'price': float(closed_trade['exit_price']),
        'qty': float(position['qty']),
        'asset_qty': float(position['qty']),
        'cash_flow': float(released_cash),
        'invested_cash_gross': float(position['invested_cash_gross']),
        'entry_fee_cash': float(position['entry_fee_cash']),
        'exit_fee_cash': float(closed_trade['exit_fee_cash']),
        'gross_sale_value': float(closed_trade['gross_sale_value']),
        'net_sale_value': float(closed_trade['net_sale_value']),
        'realized_pnl': float(closed_trade['realized_pnl']),
        'position_qty_after': 0.0,
        'avg_entry_after': float('nan'),
        'free_cash_after': float(free_cash_after),
        'equity_after': float(free_cash_after),
        'reason': reason,
        'grid_realized_pnl_after': float('nan'),
    }


def print_trade_action(
    action_row: dict[str, Any],
    free_cash: float,
    position: dict[str, Any] | None,
    realized_pnl_total: float,
    current_price: float,
    config: BacktestConfig,
) -> None:
    """Печатает краткую строку состояния после торгового действия.

    Parameters:
        action_row: Строка журнала действий.
        free_cash: Свободный кэш.
        position: Текущая открытая позиция или None.
        realized_pnl_total: Накопленный реализованный PnL.
        current_price: Актуальная рыночная цена.
        config: Конфигурация backtest.

    Returns:
        Ничего не возвращает.
    """

    invested_cash_now = float(position['invested_cash_gross']) if position is not None else 0.0
    current_pnl_now = float(position_unrealized_pnl(position, current_price, config)) if position is not None else 0.0
    grid_pnl_now = float(realized_pnl_total + current_pnl_now)
    equity_now = float(free_cash + invested_cash_now + current_pnl_now)
    realized_pnl_value = float(action_row['realized_pnl']) if np.isfinite(action_row.get('realized_pnl', np.nan)) else 0.0

    print(
        f'Trade action | ts={pd.Timestamp(action_row["timestamp"])} | action={action_row["action"]} | '
        f'side={action_row["side"]} | signal_type={action_row["signal_type"]} | price={float(action_row["price"]):.4f} | '
        f'qty={float(action_row["qty"]):.8f} | realized_pnl={realized_pnl_value:.2f} | '
        f'free_cash={free_cash:.2f} | invested_cash={invested_cash_now:.2f} | '
        f'current_pnl={current_pnl_now:.2f} | grid_pnl={grid_pnl_now:.2f} | '
        f'equity={equity_now:.2f} | reason={action_row["reason"]}'
    )


def plot_progress_chart(
    history_slice_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    current_ts: pd.Timestamp,
    retrain_hours: int,
    display_fn: DisplayFn | None = None,
) -> None:
    """Строит график прогресса backtest только для long-сделок.

    Parameters:
        history_slice_df: Срез истории до текущего момента.
        trades_df: Журнал торговых действий.
        current_ts: Текущая временная точка.
        retrain_hours: Интервал переобучения.
        display_fn: Функция отображения фигуры.

    Returns:
        Ничего не возвращает.
    """

    if len(history_slice_df) == 0:
        return

    history_view_df = history_slice_df.copy()
    history_view_df['timestamp'] = pd.to_datetime(history_view_df['timestamp'], errors='coerce', utc=True)
    history_view_df['close'] = pd.to_numeric(history_view_df['close'], errors='coerce')
    history_view_df = history_view_df.dropna(subset=['timestamp', 'close'])
    history_view_df = history_view_df.sort_values('timestamp').reset_index(drop=True)
    if len(history_view_df) == 0:
        return

    fig, ax = plt.subplots(figsize=(14, 4))
    ax.plot(history_view_df['timestamp'], history_view_df['close'], color='steelblue', linewidth=1.1, label='close')

    open_long_count = 0
    close_long_count = 0
    open_long_c_count = 0

    if len(trades_df) > 0:
        trades_view_df = trades_df.copy()
        trades_view_df['timestamp'] = pd.to_datetime(trades_view_df['timestamp'], errors='coerce', utc=True)
        trades_view_df = trades_view_df.dropna(subset=['timestamp'])
        trades_view_df = trades_view_df.loc[trades_view_df['timestamp'] <= current_ts].copy()

        open_long_df = trades_view_df.loc[trades_view_df['action'] == 'open_long'].copy()
        close_long_df = trades_view_df.loc[trades_view_df['action'] == 'close_long'].copy()
        open_long_c_df = open_long_df.loc[open_long_df['signal_type'].astype(str) == 'enter_long_C'].copy()

        open_long_count = int(len(open_long_df))
        close_long_count = int(len(close_long_df))
        open_long_c_count = int(len(open_long_c_df))

        if len(open_long_df) > 0:
            ax.scatter(
                open_long_df['timestamp'],
                open_long_df['price'],
                color='green',
                marker='^',
                s=70,
                label='open long',
                zorder=3,
            )

        if len(close_long_df) > 0:
            ax.scatter(
                close_long_df['timestamp'],
                close_long_df['price'],
                color='crimson',
                marker='v',
                s=65,
                label='close long',
                zorder=3,
            )

        if len(open_long_c_df) > 0:
            ax.scatter(
                open_long_c_df['timestamp'],
                open_long_c_df['price'],
                color='gold',
                edgecolors='black',
                marker='*',
                s=170,
                label='enter_long_C',
                zorder=4,
            )

    ax.set_title(f'Backtest progress to {current_ts} | retrain={retrain_hours}h')
    ax.set_xlabel('timestamp')
    ax.set_ylabel('price')
    ax.grid(True, alpha=0.25)
    ax.legend(loc='upper left')

    ax.text(
        0.01,
        0.02,
        (
            f'open_long={open_long_count} | close_long={close_long_count} | '
            f'enter_long_C={open_long_c_count}'
        ),
        transform=ax.transAxes,
        fontsize=9,
        color='black',
        bbox={'facecolor': 'white', 'alpha': 0.75, 'edgecolor': 'lightgray', 'boxstyle': 'round,pad=0.25'},
    )

    x_min = pd.Timestamp(history_view_df['timestamp'].iloc[0])
    x_max = pd.Timestamp(history_view_df['timestamp'].iloc[-1])
    if x_min < x_max:
        ax.set_xlim(x_min, x_max)

    fig.autofmt_xdate()
    plt.tight_layout()

    if display_fn is not None:
        display_fn(fig)
    else:
        plt.show()
    plt.close(fig)


def run_backtest_scenario(
    history_df: pd.DataFrame,
    run_symbol: str,
    output_dir: Path,
    catboost_best_params: dict[str, Any],
    config: BacktestConfig,
    compute_signal_thresholds: SignalThresholdsFn,
    fit_catboost_inference_model: FitModelFn,
    catboost_use_cuda: bool,
    retrain_hours: int,
    display_fn: DisplayFn | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Запускает один сценарий long-only backtest.

    Parameters:
        history_df: Подготовленная история цен.
        run_symbol: Торговый символ.
        output_dir: Путь к директории результатов.
        catboost_best_params: Лучшие параметры CatBoost.
        config: Конфигурация backtest.
        compute_signal_thresholds: Функция адаптивных порогов.
        fit_catboost_inference_model: Функция обучения модели.
        catboost_use_cuda: Флаг доступности CUDA.
        retrain_hours: Интервал переобучения.
        display_fn: Функция отображения графиков.

    Returns:
        DataFrame действий и словарь метаданных сценария.
    """

    _ = run_symbol
    _ = output_dir

    retrain_minutes = int(retrain_hours * 60)
    end_available_ts = pd.Timestamp(history_df['timestamp'].iloc[-1]).floor('min')
    requested_start_ts = end_available_ts - pd.Timedelta(days=float(config.start_days_ago))
    requested_end_ts = requested_start_ts + pd.Timedelta(days=float(config.duration_days))
    target_end_ts = min(end_available_ts, requested_end_ts)

    start_candidates = history_df.index[history_df['timestamp'] >= requested_start_ts]
    end_candidates = history_df.index[history_df['timestamp'] <= target_end_ts]
    if len(start_candidates) == 0 or len(end_candidates) == 0:
        raise RuntimeError('Не удалось найти диапазон backtest в истории.')

    start_idx = int(start_candidates[0])
    end_idx = int(end_candidates[-1])
    actual_start_ts = pd.Timestamp(history_df['timestamp'].iloc[start_idx]).floor('min')
    actual_end_ts = pd.Timestamp(history_df['timestamp'].iloc[end_idx]).floor('min')

    if start_idx < int(config.train_minutes):
        raise RuntimeError(
            f'Слишком ранний старт backtest: до него только {start_idx} строк истории, нужно минимум {config.train_minutes}.'
        )
    if end_idx <= start_idx:
        raise RuntimeError('Диапазон backtest пустой или некорректный.')

    bt_state: dict[str, Any] = {
        'free_cash': float(config.initial_capital),
        'realized_pnl': 0.0,
        'position': None,
        'last_exit_ts': None,
        'open_long_count': 0,
        'entry_c_checks': 0,
        'entry_c_hits': 0,
    }
    bt_trades: list[dict[str, Any]] = []
    bt_active_model: dict[str, Any] | None = None
    total_steps = len(range(start_idx, end_idx + 1, config.step_minutes))
    processed_steps = 0
    last_report_day = None

    print(
        f'Scenario start | mode=long_only | capital={config.initial_capital:.2f} | '
        f'entry_fraction={config.entry_fraction:.2f} | fee_pct={config.execution_fee_pct:.4f} | '
        f'retrain_every={retrain_hours}h | from={actual_start_ts} | to={actual_end_ts} | steps={total_steps}'
    )

    for idx in range(start_idx, end_idx + 1, config.step_minutes):
        row = history_df.iloc[idx]
        current_ts = pd.Timestamp(row['timestamp']).floor('min')
        current_price = float(row['close'])

        train_window = history_df.iloc[idx - int(config.train_minutes) + 1:idx + 1].reset_index(drop=True)
        train_series = train_window['close'].astype(float).reset_index(drop=True)
        needs_refit = bt_active_model is None or current_ts >= bt_active_model['expires_at']

        if needs_refit:
            model_obj = fit_catboost_inference_model(
                train_series,
                context_len=int(catboost_best_params['context_len']),
                depth=int(catboost_best_params['depth']),
                learning_rate=float(catboost_best_params['learning_rate']),
                iterations=int(catboost_best_params['iterations']),
                l2_leaf_reg=float(catboost_best_params.get('l2_leaf_reg', 3.0)),
                use_cuda=bool(catboost_best_params.get('use_cuda', catboost_use_cuda)) and not config.force_cpu,
            )
            bt_active_model = {
                'model_obj': model_obj,
                'fitted_at': current_ts,
                'expires_at': current_ts + pd.Timedelta(minutes=retrain_minutes),
            }
            print(f'Retrained model | ts={current_ts} | retrain_every={retrain_hours}h | trade_actions={len(bt_trades)}')

        forecast_horizon_minutes = max(int(config.live_forecast_horizon_minutes), int(config.entry_point_c_horizon_minutes))

        forecast_path_df = forecast_catboost_path(
            bt_active_model['model_obj'],
            history_series=train_series,
            horizon=forecast_horizon_minutes,
        )
        horizon_forecasts = extract_horizon_forecasts(
            forecast_path_df=forecast_path_df,
            current_price=current_price,
            horizons=config.live_signal_horizons,
        )
        trade_signal = build_trade_signal(
            current_price=current_price,
            forecast_path_df=forecast_path_df,
            horizon_forecasts=horizon_forecasts,
            config=config,
            compute_signal_thresholds=compute_signal_thresholds,
        )
        bt_state['entry_c_checks'] = int(bt_state['entry_c_checks']) + 1
        if bool(trade_signal.get('entry_c_trigger', False)):
            bt_state['entry_c_hits'] = int(bt_state['entry_c_hits']) + 1

        position = bt_state['position']
        close_reason = None

        if position is not None:
            # Временная упрощённая схема: все обычные точки выхода из long отключены.
            # in_min_hold = current_ts < pd.Timestamp(position['min_hold_until'])
            # target_allowed = current_ts >= pd.Timestamp(position['target_exit_eligible_at'])
            # force_exit = current_ts >= pd.Timestamp(position['force_exit_at'])
            #
            # if current_price <= float(position['stop_loss']):
            #     close_reason = 'stop_loss'
            # elif current_price >= float(position['take_profit']):
            #     close_reason = 'take_profit'
            # elif target_allowed and np.isfinite(position['target_exit_price']) and current_price >= float(position['target_exit_price']):
            #     close_reason = 'target_exit_price'
            # elif force_exit:
            #     close_reason = 'max_hold_reached'
            # elif trade_signal['long_close_sharp_trigger']:
            #     close_reason = 'sharp_drop_close_long'
            # elif not in_min_hold and trade_signal['long_exit_trigger']:
            #     close_reason = 'signal_exit_long'

            if close_reason is not None:
                released_cash, closed_trade = close_position(
                    position=position,
                    current_price=current_price,
                    current_ts=current_ts,
                    reason=close_reason,
                    config=config,
                )
                bt_state['free_cash'] = float(bt_state['free_cash'] + released_cash)
                bt_state['realized_pnl'] = float(bt_state['realized_pnl'] + closed_trade['realized_pnl'])

                trade_action_row = create_close_trade_action(
                    retrain_hours=retrain_hours,
                    current_ts=current_ts,
                    position=position,
                    closed_trade=closed_trade,
                    released_cash=released_cash,
                    free_cash_after=float(bt_state['free_cash']),
                    reason=close_reason,
                )
                bt_trades.append(trade_action_row)
                bt_state['position'] = None
                bt_state['last_exit_ts'] = current_ts

                print_trade_action(
                    trade_action_row,
                    free_cash=float(bt_state['free_cash']),
                    position=None,
                    realized_pnl_total=float(bt_state['realized_pnl']),
                    current_price=current_price,
                    config=config,
                )

        cooldown_ready = bt_state['last_exit_ts'] is None or current_ts >= pd.Timestamp(bt_state['last_exit_ts']) + pd.Timedelta(minutes=config.cooldown_minutes)

        if bt_state['position'] is None and cooldown_ready and trade_signal['long_open_trigger']:
            signal_type = trade_signal['signal_type'] if trade_signal['signal_type'] != 'none' else 'trend_long'
            new_position = open_position(
                signal_type=signal_type,
                current_price=current_price,
                current_ts=current_ts,
                target_exit_price=float(trade_signal['target_exit_price']),
                free_cash=float(bt_state['free_cash']),
                fraction=float(config.entry_fraction),
                config=config,
            )

            if new_position is not None:
                bt_state['free_cash'] = float(bt_state['free_cash'] - new_position['invested_cash_gross'])
                bt_state['position'] = new_position
                bt_state['open_long_count'] = int(bt_state['open_long_count']) + 1

                trade_action_row = create_open_trade_action(
                    retrain_hours=retrain_hours,
                    current_ts=current_ts,
                    position=new_position,
                    free_cash_after=float(bt_state['free_cash']),
                    realized_pnl_total=float(bt_state['realized_pnl']),
                    current_price=current_price,
                    reason=trade_signal['entry_reason'],
                    config=config,
                )
                bt_trades.append(trade_action_row)

                print_trade_action(
                    trade_action_row,
                    free_cash=float(bt_state['free_cash']),
                    position=bt_state['position'],
                    realized_pnl_total=float(bt_state['realized_pnl']),
                    current_price=current_price,
                    config=config,
                )

        processed_steps += 1
        current_day = current_ts.normalize()
        invested_cash_now = float(bt_state['position']['invested_cash_gross']) if bt_state['position'] is not None else 0.0
        current_pnl_now = float(position_unrealized_pnl(bt_state['position'], current_price, config)) if bt_state['position'] is not None else 0.0
        grid_pnl_now = float(bt_state['realized_pnl'] + current_pnl_now)
        equity_now = float(bt_state['free_cash'] + invested_cash_now + current_pnl_now)

        if last_report_day is None or current_day > last_report_day:
            last_report_day = current_day
            print(
                f'Progress | date={current_day.date()} | '
                f'step={processed_steps}/{total_steps} | trade_actions={len(bt_trades)} | '
                f'entry_c_hits={int(bt_state["entry_c_hits"])}/{int(bt_state["entry_c_checks"])} | '
                f'free_cash={bt_state["free_cash"]:.2f} | invested_cash={invested_cash_now:.2f} | '
                f'current_pnl={current_pnl_now:.2f} | grid_pnl={grid_pnl_now:.2f} | equity={equity_now:.2f}'
            )
            plot_progress_chart(
                history_slice_df=history_df.iloc[start_idx:idx + 1][['timestamp', 'close']].copy(),
                trades_df=pd.DataFrame(bt_trades),
                current_ts=current_ts,
                retrain_hours=int(retrain_hours),
                display_fn=display_fn,
            )

    if bt_state['position'] is not None:
        last_row = history_df.iloc[end_idx]
        final_ts = pd.Timestamp(last_row['timestamp']).floor('min')
        final_price = float(last_row['close'])
        position = bt_state['position']

        released_cash, closed_trade = close_position(
            position=position,
            current_price=final_price,
            current_ts=final_ts,
            reason='backtest_end',
            config=config,
        )
        bt_state['free_cash'] = float(bt_state['free_cash'] + released_cash)
        bt_state['realized_pnl'] = float(bt_state['realized_pnl'] + closed_trade['realized_pnl'])

        trade_action_row = create_close_trade_action(
            retrain_hours=retrain_hours,
            current_ts=final_ts,
            position=position,
            closed_trade=closed_trade,
            released_cash=released_cash,
            free_cash_after=float(bt_state['free_cash']),
            reason='backtest_end',
        )
        bt_trades.append(trade_action_row)
        bt_state['position'] = None
        bt_state['last_exit_ts'] = final_ts

        print_trade_action(
            trade_action_row,
            free_cash=float(bt_state['free_cash']),
            position=None,
            realized_pnl_total=float(bt_state['realized_pnl']),
            current_price=final_price,
            config=config,
        )

    trades_df = pd.DataFrame(bt_trades)
    if len(trades_df) == 0:
        trades_df = pd.DataFrame(columns=[
            'retrain_hours', 'timestamp', 'action', 'side', 'signal_type', 'price', 'qty', 'asset_qty', 'cash_flow',
            'invested_cash_gross', 'entry_fee_cash', 'exit_fee_cash', 'gross_sale_value', 'net_sale_value',
            'realized_pnl', 'position_qty_after', 'avg_entry_after', 'free_cash_after', 'equity_after', 'reason',
            'grid_realized_pnl_after',
        ])

    close_trades_df = trades_df[trades_df['action'].astype(str).str.startswith('close_')].copy() if len(trades_df) > 0 else pd.DataFrame()
    scenario_meta = {
        'retrain_hours': int(retrain_hours),
        'requested_start_ts': requested_start_ts,
        'requested_end_ts': requested_end_ts,
        'actual_start_ts': actual_start_ts,
        'actual_end_ts': actual_end_ts,
        'effective_duration_days': float((actual_end_ts - actual_start_ts).total_seconds() / 86_400.0),
        'eval_step_minutes': int(config.step_minutes),
        'initial_capital': float(config.initial_capital),
        'entry_fraction': float(config.entry_fraction),
        'execution_fee_pct': float(config.execution_fee_pct),
        'trade_actions': int(len(trades_df)),
        'closed_trades': int(len(close_trades_df)),
        'opened_longs': int(bt_state['open_long_count']),
        'entry_c_checks': int(bt_state['entry_c_checks']),
        'entry_c_hits': int(bt_state['entry_c_hits']),
        'entry_c_hit_rate': float(bt_state['entry_c_hits'] / bt_state['entry_c_checks']) if int(bt_state['entry_c_checks']) > 0 else float('nan'),
        'entry_c_horizon_minutes': int(config.entry_point_c_horizon_minutes),
        'entry_c_move_pct': float(config.entry_point_c_move_pct),
        'ending_equity': float(bt_state['free_cash']),
        'ending_realized_pnl': float(bt_state['realized_pnl']),
    }
    return trades_df, scenario_meta


def run_backtest_block(
    history_df: pd.DataFrame,
    run_symbol: str,
    output_dir: Path,
    catboost_best_params: dict[str, Any],
    config: BacktestConfig,
    compute_signal_thresholds: SignalThresholdsFn,
    fit_catboost_inference_model: FitModelFn,
    catboost_use_cuda: bool,
    display_fn: DisplayFn | None = None,
) -> dict[str, Any]:
    """Запускает весь упрощённый long-only backtest.

    Parameters:
        history_df: Сырой DataFrame с колонками timestamp и close.
        run_symbol: Торговый символ.
        output_dir: Каталог для CSV-результатов.
        catboost_best_params: Лучшие параметры CatBoost.
        config: Конфигурация backtest.
        compute_signal_thresholds: Функция адаптивных порогов.
        fit_catboost_inference_model: Функция обучения модели.
        catboost_use_cuda: Флаг доступности CUDA.
        display_fn: Функция отображения таблиц и графиков.

    Returns:
        Словарь с основными артефактами backtest.
    """

    normalized_history = normalize_backtest_history(history_df)
    validate_backtest_inputs(normalized_history, config)

    backtest_results: list[pd.DataFrame] = []
    backtest_meta_rows: list[dict[str, Any]] = []

    print(f'Backtest symbol={run_symbol} | start_days_ago={config.start_days_ago} | duration_days={config.duration_days}')
    print(
        f'Train minutes={config.train_minutes} | retrain_every_hours={config.retrain_every_hours} | '
        f'eval_step_minutes={config.step_minutes}'
    )
    print(
        f'Mode=long_only | initial_capital={config.initial_capital:.2f} | '
        f'entry_fraction={config.entry_fraction:.2f} | execution_fee_pct={config.execution_fee_pct:.4f}'
    )

    if int(config.duration_days) > int(config.start_days_ago):
        print(
            'Note: duration_days > start_days_ago, поэтому конец окна будет обрезан последней доступной точкой истории. '
            'Для полного окна обычно ставь start_days_ago >= duration_days.'
        )

    for retrain_hours in [int(config.retrain_every_hours)]:
        scenario_trades_df, scenario_meta = run_backtest_scenario(
            history_df=normalized_history,
            run_symbol=run_symbol,
            output_dir=output_dir,
            catboost_best_params=catboost_best_params,
            config=config,
            compute_signal_thresholds=compute_signal_thresholds,
            fit_catboost_inference_model=fit_catboost_inference_model,
            catboost_use_cuda=catboost_use_cuda,
            retrain_hours=int(retrain_hours),
            display_fn=display_fn,
        )
        backtest_results.append(scenario_trades_df)
        backtest_meta_rows.append(scenario_meta)

    backtest_trades_df = pd.concat(backtest_results, ignore_index=True) if len(backtest_results) > 0 else pd.DataFrame()
    backtest_meta_df = pd.DataFrame(backtest_meta_rows).sort_values('retrain_hours').reset_index(drop=True)
    backtest_trades_path = output_dir / f"{run_symbol}_catboost_backtest_trades_{pd.Timestamp.now(tz='UTC').strftime('%Y%m%d_%H%M%S')}.csv"

    if config.save_to_csv and len(backtest_trades_df) > 0:
        backtest_trades_df.to_csv(backtest_trades_path, index=False)

    backtest_trades_view_df = backtest_trades_df[[
        'retrain_hours', 'timestamp', 'action', 'side', 'signal_type', 'price', 'qty', 'cash_flow', 'realized_pnl', 'free_cash_after', 'equity_after', 'reason'
    ]].copy() if len(backtest_trades_df) > 0 else pd.DataFrame()

    daily_activity_rows: list[dict[str, Any]] = []
    for _, meta_row in backtest_meta_df.iterrows():
        retrain_hours = int(meta_row['retrain_hours'])
        day_range = pd.date_range(
            pd.Timestamp(meta_row['actual_start_ts']).normalize(),
            pd.Timestamp(meta_row['actual_end_ts']).normalize(),
            freq='D',
            tz='UTC',
        )
        scenario_trades_df = backtest_trades_df.loc[backtest_trades_df['retrain_hours'] == retrain_hours].copy() if len(backtest_trades_df) > 0 else pd.DataFrame()
        if len(scenario_trades_df) > 0:
            scenario_trades_df['trade_day'] = pd.to_datetime(scenario_trades_df['timestamp'], utc=True).dt.normalize()
            day_counts = scenario_trades_df.groupby('trade_day').size().to_dict()
        else:
            day_counts = {}

        for day_ts in day_range:
            trade_count = int(day_counts.get(day_ts, 0))
            daily_activity_rows.append({
                'retrain_hours': retrain_hours,
                'date': day_ts,
                'trade_actions': trade_count,
                'status': 'нет торгов' if trade_count == 0 else 'есть сделки',
            })

    backtest_daily_activity_df = pd.DataFrame(daily_activity_rows)

    print('\nBacktest scenarios:')
    if display_fn is not None:
        display_fn(backtest_meta_df)
    else:
        print(backtest_meta_df)

    if len(backtest_daily_activity_df) > 0:
        no_trade_days_df = backtest_daily_activity_df.loc[backtest_daily_activity_df['trade_actions'] == 0].reset_index(drop=True)
        if len(no_trade_days_df) > 0:
            print('\nДни без торгов:')
            if display_fn is not None:
                display_fn(no_trade_days_df)
            else:
                print(no_trade_days_df)
        else:
            print('\nВ каждом дне тестового диапазона были торговые действия.')

    if len(backtest_trades_view_df) == 0:
        print('\nTrades: no trading actions were generated for the selected range.')
    else:
        print(f'\nTrades shown: last {min(len(backtest_trades_view_df), int(config.display_max_trades))} rows (full log is saved to CSV if enabled).')
        if display_fn is not None:
            display_fn(backtest_trades_view_df.tail(int(config.display_max_trades)).reset_index(drop=True))
        else:
            print(backtest_trades_view_df.tail(int(config.display_max_trades)).reset_index(drop=True))

    if config.save_to_csv and len(backtest_trades_df) > 0:
        print('\nSaved backtest trades to:')
        print(backtest_trades_path.resolve())

    return {
        'history_df': normalized_history,
        'backtest_results': backtest_results,
        'backtest_meta_rows': backtest_meta_rows,
        'backtest_trades_df': backtest_trades_df,
        'backtest_meta_df': backtest_meta_df,
        'backtest_trades_view_df': backtest_trades_view_df,
        'backtest_daily_activity_df': backtest_daily_activity_df,
        'backtest_trades_path': backtest_trades_path,
    }