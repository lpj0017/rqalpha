# -*- coding: utf-8 -*-

import sys

from six import iteritems
import pandas as pd

from .analyser import Position, Portfolio, Order
from .analyser.commission import AStockCommission
from .analyser.simulation_exchange import SimuExchange
from .analyser.slippage import FixedPercentSlippageDecider
from .analyser.tax import AStockTax
from .const import EVENT_TYPE, EXECUTION_PHASE
from .data import BarMap, RqDataProxy
from .events import SimulatorAStockTradingEventSource
from .instruments import Instrument
from .utils import ExecutionContext


class StrategyContext(object):
    def __init__(self):
        pass

    @property
    def now(self):
        return ExecutionContext.get_current_dt()

    @property
    def slippage(self):
        raise NotImplementedError

    @slippage.setter
    def slippage(self):
        raise NotImplementedError

    @property
    def commission(self):
        raise NotImplementedError

    @commission.setter
    def commission(self, value):
        raise NotImplementedError

    @property
    def benchmark(self):
        raise NotImplementedError

    @benchmark.setter
    def benchmark(self):
        raise NotImplementedError

    @property
    def short_selling_allowed(self):
        raise NotImplementedError

    @short_selling_allowed.setter
    def short_selling_allowed(self):
        raise NotImplementedError

    @property
    def portfolio(self):
        return ExecutionContext.get_exchange().account.portfolio

    def __repr__(self):
        items = ("%s = %r" % (k, v) for k, v in self.__dict__.items() if not callable(v))
        return "Context({%s})" % (', '.join(items), )


class StrategyExecutor(object):
    def __init__(self, trading_params, data_proxy, **kwargs):
        """init

        :param Strategy strategy: current user strategy object
        :param TradingParams trading_params: current trading params
        :param DataProxy data_proxy: current data proxy to access data
        """
        self.trading_params = trading_params
        self._data_proxy = data_proxy

        self._strategy_context = kwargs.get("strategy_context")
        if self._strategy_context is None:
            self._strategy_context = StrategyContext()

        dummy_func = lambda *args, **kwargs: None

        self._user_init = kwargs.get("init", dummy_func)
        self._user_handle_bar = kwargs.get("handle_bar", dummy_func)
        self._user_before_trading = kwargs.get("before_trading", dummy_func)

        self._simu_exchange = kwargs.get("simu_exchange")
        if self._simu_exchange is None:
            self._simu_exchange = SimuExchange(data_proxy, trading_params)

        self._event_source = SimulatorAStockTradingEventSource(trading_params)
        self._current_dt = None
        self.current_universe = set()

    def execute(self):
        """run strategy

        :returns: performance results
        :rtype: pandas.DataFrame

        """
        # use local variable for performance
        data_proxy = self.data_proxy
        strategy_context = self.strategy_context
        simu_exchange = self.exchange

        init = self._user_init
        before_trading = self._user_before_trading
        handle_bar = self._user_handle_bar

        exchange_on_dt_change = simu_exchange.on_dt_change
        exchange_on_bar_close = simu_exchange.on_bar_close
        exchange_on_day_open = simu_exchange.on_day_open
        exchange_on_day_close = simu_exchange.on_day_close

        def on_dt_change(dt):
            self._current_dt = dt
            exchange_on_dt_change(dt)

        with ExecutionContext(self, EXECUTION_PHASE.INIT):
            init(strategy_context)

        for dt, event in self._event_source:
            on_dt_change(dt)

            bar_dict = BarMap(dt, self.current_universe, data_proxy)

            if event == EVENT_TYPE.DAY_START:
                with ExecutionContext(self, EXECUTION_PHASE.BEFORE_TRADING, bar_dict):
                    exchange_on_day_open()
                    before_trading(strategy_context, None)

            elif event == EVENT_TYPE.HANDLE_BAR:
                with ExecutionContext(self, EXECUTION_PHASE.HANDLE_BAR, bar_dict):
                    handle_bar(strategy_context, bar_dict)
                    exchange_on_bar_close(bar_dict)

            elif event == EVENT_TYPE.DAY_END:
                exchange_on_day_close()

        # prepare backtest results
        account = simu_exchange.account
        risk_cal = simu_exchange.risk_cal
        columns = [
            "daily_returns",
            "total_returns",
            "annualized_returns",
            "market_value",
            "portfolio_value",
            "total_commission",
            "total_tax",
            "pnl",
            "positions",
            "cash",
        ]
        data = []
        for date, portfolio in iteritems(simu_exchange.daily_portfolios):
            # portfolio
            items = {"date": pd.Timestamp(date)}
            for key in columns:
                items[key] = getattr(portfolio, key)

            # trades
            items["trades"] = account.get_all_trades()[date]

            # risk
            risk = risk_cal.daily_risks[date]
            risk_keys = ["volatility", "max_drawdown",
                         "alpha", "beta", "sharpe",
                         # "information_rate", "downside_risk",
                         ]
            for risk_key in risk_keys:
                items[risk_key] = getattr(risk, risk_key)

            idx = risk_cal.trading_index.get_loc(date)
            items["benchmark_total_returns"] = risk_cal.benchmark_total_returns[idx]
            items["benchmark_daily_returns"] = risk_cal.benchmark_total_daily_returns[idx]

            data.append(items)

        results_df = pd.DataFrame(data)
        results_df.set_index("date", inplace=True)

        return results_df

    @property
    def strategy_context(self):
        """get current strategy

        :returns: current strategy
        :rtype: Strategy
        """
        return self._strategy_context

    @property
    def exchange(self):
        """get current exchange

        :returns: current exchange
        :rtype: SimuExchange
        """
        return self._simu_exchange

    @property
    def data_proxy(self):
        """get current data proxy

        :returns: current data proxy
        :rtype: DataProxy
        """
        return self._data_proxy

    @property
    def current_dt(self):
        """get current simu datetime

        :returns: current datetime
        :rtype: datetime.datetime
        """
        return self._current_dt