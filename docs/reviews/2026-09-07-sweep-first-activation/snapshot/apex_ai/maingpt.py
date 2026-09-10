"""
APEX AI Trading System — Main Entry Point
Boots all engines, agents, and fund OS. Runs the autonomous analysis loop.
"""
import os
import sys
import json
import time
import logging
import argparse
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional

# ── Path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

# ── Logging ───────────────────────────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)
Path("data").mkdir(exist_ok=True)
import io
_stream_handler = logging.StreamHandler(io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace'))
_stream_handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        RotatingFileHandler("logs/apex_ai.log", encoding="utf-8",
                            maxBytes=10 * 1024 * 1024, backupCount=5),
        _stream_handler,
    ]
)
logger = logging.getLogger("APEX")

# ── Core imports ──────────────────────────────────────────────────────────────
from core.liquidity_engine    import LiquidityEngine, LiquidityMap
from core.manipulation_engine import ManipulationEngine, ManipulationSignal
from core.structure_engine    import StructureEngine, StructureState
from core.displacement_engine import DisplacementEngine, DisplacementMap
from core.execution_models    import ExecutionModels, TradeSetup

from market.mt5_connector  import MT5Connector
from market.session_engine import SessionEngine
from market.multi_timeframe import MultiTimeframeEngine

from intelligence.probabilistic_engine import ProbabilisticEngine
from intelligence.regime_engine        import RegimeEngine
from intelligence.order_flow_engine    import OrderFlowEngine
from intelligence.portfolio_engine     import PortfolioEngine
from intelligence.risk_engine          import RiskEngine
from intelligence.adaptive_memory      import AdaptiveMemory, TradeRecord

from agents.lia  import LiquidityIntelligenceAgent,  LIAContext
from agents.msa  import MarketStructureAgent,         MSAContext
from agents.rda  import RegimeDetectionAgent,         RDAContext
from agents.caia import CrossAssetIntelligenceAgent,  CAIAContext
from agents.see  import StrategyEvolutionEngine,      SEEContext
from agents.ea   import ExecutionAgent,               EAContext
from agents.crg  import CapitalRiskGovernor,          CRGContext

from fund.hedge_fund_os     import HedgeFundOS
from fund.simulation_engine import SimulationEngine
from fund.compounding_engine import CompoundingEngine
from fund.capital_governance import CapitalGovernance
from fund.meta_fund         import MetaFundEngine

from dashboard.terminal_ui import TerminalDashboard


# ══════════════════════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════════════════════

def load_config(path: str = "config.json") -> Dict:
    with open(path, "r") as f:
        return json.load(f)


# ══════════════════════════════════════════════════════════════════════════════
# APEX AI Core Loop
# ══════════════════════════════════════════════════════════════════════════════

class APEXTradingSystem:
    """
    APEX AI Trading System — the central orchestrator.
    Implements the full v1→v11 intelligence stack from claude.md.
    """

    TIMEFRAMES = ["D1", "H4", "H1", "M15", "M5"]
    EXECUTION_TF = "M15"   # Timeframe used for execution signal

    def __init__(self, config: Dict, dry_run: bool = False):
        self.config   = config
        self.dry_run  = dry_run or config.get("dry_run", False)
        self.symbols  = config["symbols"]
        self.interval = config.get("analysis_interval_seconds", 60)
        self._start_time = datetime.now(timezone.utc)
        self._cycle   = 0
        self._trade_count = 0
        self._last_exec   = "None"
        self._peak_balance = 0.0
        # ticket -> last-seen snapshot, used to detect closures for journalling
        self._tracked_positions: Dict[int, Dict] = {}

        # ── v1 Core Engines
        self.liquidity     = LiquidityEngine(swing_lookback=config.get("swing_lookback", 5))
        _manip_cfg = config.get("manipulation", {})
        # Strip _doc_ keys before passing to engine
        _sym_pct = {k: v for k, v in _manip_cfg.get("symbol_sweep_pct", {}).items()
                    if not k.startswith("_doc")}
        self.manipulation  = ManipulationEngine(
            sweep_wick_pct=_manip_cfg.get("sweep_wick_pct_default", 0.001),
            reversal_bars=_manip_cfg.get("reversal_bars", 3),
            symbol_sweep_pct=_sym_pct)
        self.structure_eng = StructureEngine(swing_lookback=config.get("swing_lookback", 5))
        self.displacement  = DisplacementEngine()
        self.exec_models   = ExecutionModels(min_rr=config["risk"]["min_rr_ratio"])

        # ── Market layer
        self.connector  = MT5Connector(dry_run=self.dry_run)
        self.session    = SessionEngine()
        self.mtf_engine = MultiTimeframeEngine(swing_lookback=config.get("swing_lookback", 5))

        # ── Intelligence layer
        self.prob_engine  = ProbabilisticEngine()
        self.regime_eng   = RegimeEngine()
        self.of_engine    = OrderFlowEngine()
        self.port_engine  = PortfolioEngine(
            max_total_risk_pct=config["risk"]["max_total_exposure_pct"],
            max_positions=config["risk"]["max_positions"])
        self.risk_engine  = RiskEngine(
            high_risk_pct=config["risk"]["high_clarity_risk_pct"],
            medium_risk_pct=config["risk"]["medium_clarity_risk_pct"],
            low_risk_pct=config["risk"]["low_clarity_risk_pct"],
            min_rr=config["risk"]["min_rr_ratio"],
            single_trade_dollar_cap_pct=config.get("single_trade_dollar_cap_pct", 5.0))
        self.memory = AdaptiveMemory()

        # ── Multi-Agent System (v9)
        self.lia  = LiquidityIntelligenceAgent()
        self.msa  = MarketStructureAgent()
        self.rda  = RegimeDetectionAgent()
        self.caia = CrossAssetIntelligenceAgent()
        self.see  = StrategyEvolutionEngine()
        self.ea   = ExecutionAgent()
        self.crg  = CapitalRiskGovernor(
            governance_limits=config.get("governance_limits"))

        # ── Hedge Fund OS (v10)
        self.fund_os    = HedgeFundOS(
            min_agent_consensus=config.get("agent_consensus_threshold", 5),
            max_positions=config["risk"]["max_positions"],
            max_total_risk_pct=config["risk"]["max_total_exposure_pct"])
        self.simulation = SimulationEngine(
            monte_carlo_runs=config["simulation"]["monte_carlo_runs"],
            min_win_prob=config["simulation"]["min_win_prob_to_trade"])
        self.governance = CapitalGovernance(
            default_mode=config["governance"]["default_mode"],
            aggressive_dd=config["governance"]["aggressive_dd_threshold"],
            balanced_dd=config["governance"]["balanced_dd_threshold"],
            defensive_dd=config["governance"]["defensive_dd_threshold"],
            preservation_dd=config["governance"]["preservation_dd_threshold"])
        self.compounding = CompoundingEngine()
        self.meta_fund   = MetaFundEngine(self.memory,
            rebalance_interval=config["meta_fund"]["rebalance_interval_trades"],
            lookback_trades=config["meta_fund"]["lookback_trades"])

        # ── News blackout (was scalper-only; Pool A traded straight through
        #    high-impact releases despite the docs claiming system-wide cover)
        self.news_guard = NewsGuard(
            events_file=str(Path(__file__).parent / "news_events.json"),
            blackout_before_min=30,
            blackout_after_min=15,
            impact_levels=["HIGH"])
        self._last_news_fetch: Optional[datetime] = None

        # ── Dashboard
        self.dashboard = TerminalDashboard()

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self):
        self.dashboard.print_startup_banner()
        logger.info(f"APEX AI starting | Symbols: {self.symbols} | DryRun: {self.dry_run}")

        if not self.connector.connect():
            logger.error("Failed to connect to MT5. Exiting.")
            sys.exit(1)

        acct = self.connector.get_account_info()
        self._peak_balance = acct.get("balance", 10000.0)
        self.compounding = CompoundingEngine(initial_balance=self._peak_balance)

        logger.info(f"Account: ${acct.get('balance', 0):,.2f} {acct.get('currency', '')}")

        try:
            with self.dashboard.start_live() as live:
                while True:
                    try:
                        dashboard_state = self._run_cycle()
                        self.dashboard.update_live(dashboard_state)
                        time.sleep(self.interval)
                    except KeyboardInterrupt:
                        break
                    except Exception as e:
                        logger.error(f"Cycle error: {e}", exc_info=True)
                        time.sleep(10)
        finally:
            self.connector.disconnect()
            logger.info("APEX AI shut down cleanly.")

    # ── Analysis Cycle ────────────────────────────────────────────────────

    def _run_cycle(self) -> Dict[str, Any]:
        self._cycle += 1
        now = datetime.now(timezone.utc)
        logger.info(f"=== Cycle #{self._cycle} at {now.strftime('%H:%M:%S UTC')} ===")

        # Session state
        sess_state = self.session.get_state(now)
        next_kz    = self.session.next_kill_zone(now)

        # Account + governance
        acct = self.connector.get_account_info()
        balance = acct.get("balance", self._peak_balance)
        equity  = acct.get("equity", balance)
        self._peak_balance = max(self._peak_balance, balance)

        gov_state  = self.governance.assess(balance, self._peak_balance)
        gov_mode   = str(gov_state.mode.value)
        compound   = self.compounding.update(balance, equity)

        # Refresh the economic calendar so the blackout does not go stale when
        # the scalper (its other consumer) is not running.
        self._refresh_news_if_due(now)

        # Open positions + portfolio state
        positions  = self.connector.get_open_positions()
        portfolio  = self.port_engine.assess(positions)

        # Detect closures and write them to the trade journal
        self._journal_closed_positions(positions, now)

        # Per-symbol analysis
        analyses: Dict[str, Any] = {}
        all_structures: Dict[str, StructureState] = {}
        agent_votes_by_symbol: Dict[str, Any] = {}
        consensus_score = 0.0

        for symbol in self.symbols:
            result = self._analyze_symbol(
                symbol, sess_state, gov_mode, gov_state.daily_dd_pct,
                portfolio, compound, acct)

            analyses[symbol] = result["summary"]
            all_structures[symbol] = result["structure"]
            agent_votes_by_symbol = result["agent_votes"]  # Use last symbol's for display
            consensus_score = result.get("consensus_score", 0.0)

            # Execute if approved and no high-impact release is imminent
            blackout = self.news_guard.active_blackout(now_utc=now, symbol=symbol)
            if blackout and result.get("approved"):
                logger.warning(
                    f"{symbol}: EXECUTION BLOCKED — news blackout for "
                    f"{blackout['impact']} '{blackout['title']}' "
                    f"({blackout['window_start_utc']} -> {blackout['window_end_utc']})")
                result["approved"] = False
                result["summary"]["decision"] = "WAIT"

            if result.get("approved") and result.get("setup"):
                self._execute_trade(result["setup"], result["risk"],
                                    symbol, result["connector_info"])
                self._last_exec = f"{symbol} {result['setup'].direction} {now.strftime('%H:%M')}"

        # Meta-fund state
        self.meta_fund.update(self._trade_count)
        meta_state = self.meta_fund.get_state()
        meta_fund_display = {
            name: {"capital_weight": f.capital_weight, "win_rate": f.win_rate,
                   "status": f.status}
            for name, f in meta_state.sub_funds.items()
        }

        # System behavior state
        first_analysis = next(iter(analyses.values()), {})
        system_state = first_analysis.get("behavior_state", "OBSERVE")

        uptime_secs = int((now - self._start_time).total_seconds())
        h, r = divmod(uptime_secs, 3600)
        m, _ = divmod(r, 60)

        return {
            "analyses": analyses,
            "agent_votes": agent_votes_by_symbol,
            "consensus_score": consensus_score,
            "account": acct,
            "compounding": {
                "current_drawdown_pct": compound.current_drawdown_pct,
                "max_drawdown_pct": compound.max_drawdown_pct,
                "growth_pct": compound.growth_pct,
                "daily_pnl": compound.daily_pnl,
                "suggested_risk_pct": compound.suggested_risk_pct,
            },
            "positions": positions,
            "session": {
                "active_session": sess_state.active_session,
                "is_kill_zone": sess_state.is_kill_zone,
            },
            "governance_mode": gov_mode,
            "system_state": system_state,
            "meta_fund": meta_fund_display,
            "next_kz": next_kz,
            "cycle_count": self._cycle,
            "last_execution": self._last_exec,
            "uptime": f"{h}h {m}m",
        }

    def _analyze_symbol(self, symbol: str, sess_state, gov_mode: str,
                        dd_pct: float, portfolio, compound, acct) -> Dict:
        """Full v1→v11 analysis pipeline for one symbol."""
        result: Dict[str, Any] = {
            "summary": {"price": 0, "regime": "UNKNOWN", "dominant": "RANGING",
                        "bull_pct": 0, "bear_pct": 0, "structure_trend": "RANGING",
                        "session_weight": sess_state.session_weight, "decision": "WAIT",
                        "clarity": "LOW", "behavior_state": "OBSERVE"},
            "structure": StructureState(symbol=symbol, timeframe="H1"),
            "agent_votes": {},
            "approved": False,
            "setup": None,
            "risk": None,
            "connector_info": {},
        }

        # ── Fetch OHLCV ───────────────────────────────────────────────────
        ohlcv_data = {}
        for tf in self.TIMEFRAMES:
            df = self.connector.get_ohlcv(symbol, tf, bars=200)
            if df is not None and len(df) > 20:
                ohlcv_data[tf] = df

        exec_df = ohlcv_data.get(self.EXECUTION_TF)
        if exec_df is None or len(exec_df) < 20:
            logger.debug(f"Insufficient data for {symbol}")
            return result

        current_price = float(exec_df['close'].iloc[-1])
        result["summary"]["price"] = current_price

        # ── v1: Core Engines ──────────────────────────────────────────────
        lmap     = self.liquidity.analyze(exec_df, symbol, self.EXECUTION_TF)
        manip    = self.manipulation.analyze(exec_df, lmap)
        struct_h1 = self.structure_eng.analyze(
            ohlcv_data.get("H1", exec_df), symbol, "H1")
        disp     = self.displacement.analyze(exec_df, symbol, self.EXECUTION_TF)
        of_state = self.of_engine.analyze(exec_df)

        # ── v3: Multi-timeframe ───────────────────────────────────────────
        mtf = self.mtf_engine.analyze(symbol, ohlcv_data)

        # ── v4-v5: Intelligence ───────────────────────────────────────────
        bias   = self.prob_engine.analyze(lmap, manip, struct_h1, disp, mtf, sess_state)
        regime = self.regime_eng.analyze(exec_df, struct_h1, manip, disp, dd_pct)

        result["structure"] = struct_h1
        result["summary"].update({
            "regime":           regime.regime,
            "dominant":         bias.dominant,
            "bull_pct":         bias.bullish_pct,
            "bear_pct":         bias.bearish_pct,
            "structure_trend":  struct_h1.trend,
            "order_flow":       of_state.control,
            "order_flow_pressure": of_state.pressure,
            "clarity":          bias.clarity,
            "behavior_state":   regime.behavior_state,
        })

        # ── v9: Multi-Agent Voting ────────────────────────────────────────
        lia_vote  = self.lia.analyze(LIAContext(lmap=lmap, manip=manip, current_price=current_price))
        msa_vote  = self.msa.analyze(MSAContext(structure=struct_h1, mtf=mtf))
        rda_vote  = self.rda.analyze(RDAContext(regime=regime, daily_dd_pct=dd_pct))
        caia_vote = self.caia.analyze(CAIAContext(
            structures={s: self.structure_eng.analyze(
                ohlcv_data.get("H1", exec_df), s, "H1")
                for s in self.symbols},
            primary_symbol=symbol))

        # Try to build a setup for SEE evaluation
        atr_val = disp._calc_atr(exec_df) if hasattr(disp, '_calc_atr') else (current_price * 0.001)
        setup_candidate = self.exec_models.evaluate(
            symbol, current_price, atr_val, lmap, manip, struct_h1, disp)

        see_vote = self.see.analyze(SEEContext(
            symbol=symbol,
            proposed_model=setup_candidate.model_type if setup_candidate else "RETURN",
            memory=self.memory))

        agent_votes = {
            "LIA": lia_vote, "MSA": msa_vote, "RDA": rda_vote,
            "CAIA": caia_vote, "SEE": see_vote
        }

        # ── Risk calculation ──────────────────────────────────────────────
        sym_info = self.connector.get_symbol_info(symbol) or {}
        point_val     = sym_info.get("point", 0.01)
        contract_size = sym_info.get("trade_contract_size", 100.0)

        if setup_candidate:
            risk = self.risk_engine.calculate(
                account_balance=acct.get("balance", 10000),
                entry_price=setup_candidate.entry_price,
                stop_loss=setup_candidate.stop_loss,
                take_profit=setup_candidate.take_profit,
                point_value=point_val,
                contract_size=contract_size,
                bias=bias, regime=regime,
                session=sess_state, governance_mode=gov_mode)
        else:
            risk = None

        # ── EA + CRG votes ────────────────────────────────────────────────
        if setup_candidate and risk:
            ea_ctx = EAContext(setup=setup_candidate, risk=risk,
                               connector=self.connector, dry_run=self.dry_run)
            ea_vote = self.ea.analyze(ea_ctx)

            crg_ctx = CRGContext(
                symbol=symbol, direction=setup_candidate.direction,
                risk_pct=risk.risk_pct, portfolio=portfolio,
                governance_mode=gov_mode, daily_dd_pct=dd_pct,
                max_dd_pct=self.config["risk"]["max_drawdown_pct"],
                max_total_risk_pct=self.config["risk"]["max_total_exposure_pct"],
                max_positions=self.config["risk"]["max_positions"])
            crg_vote = self.crg.analyze(crg_ctx)
        else:
            ea_vote  = self.ea._make_vote("ABSTAIN", 1.0, "No valid setup")
            crg_vote = self.crg._make_vote("ABSTAIN", 1.0, "No valid setup")

        agent_votes["EA"]  = ea_vote
        agent_votes["CRG"] = crg_vote

        # Format for dashboard
        votes_display = {
            name: {"vote": v.vote, "confidence": v.confidence, "reasoning": v.reasoning}
            for name, v in agent_votes.items()
        }
        result["agent_votes"] = votes_display

        # Consensus score
        if setup_candidate:
            expected = "BULLISH" if setup_candidate.direction == "BUY" else "BEARISH"
            aligned = sum(1 for v in agent_votes.values()
                          if v.vote == expected or
                          (v.vote == "NEUTRAL" and v.confidence >= 0.55))
            result["consensus_score"] = aligned / len(agent_votes)
        else:
            result["consensus_score"] = 0.0

        # ── v10: 5-Gate Approval ──────────────────────────────────────────
        if setup_candidate and risk and risk.approved:
            sim_result = self.simulation.simulate(
                setup_candidate, bias, risk.dollar_risk)

            # Gate 2 previously received only the five non-execution agents
            # while the threshold stayed at 5, so "5 of 7" silently became
            # "5 of 5" — unanimity plus zero abstentions. EA and CRG now vote
            # in the consensus count too, matching the documented rule. CRG
            # still holds its independent veto at Gate 3.
            approval = self.fund_os.evaluate(
                setup=setup_candidate,
                simulation=sim_result,
                agent_votes=dict(agent_votes),
                crg_vote=crg_vote,
                risk=risk,
                portfolio=portfolio,
                regime_name=regime.regime,
                preferred_model=regime.preferred_model)

            decision = "EXECUTE" if approval.approved else "WAIT"
            result["summary"]["decision"] = decision
            result["approved"]  = approval.approved
            result["setup"]     = setup_candidate
            result["risk"]      = risk
            result["connector_info"] = sym_info
        else:
            result["summary"]["decision"] = "WAIT"

        logger.info(
            f"{symbol}: {result['summary']['regime']} | "
            f"Bias={bias.dominant}({bias.bullish_pct:.0f}%B/{bias.bearish_pct:.0f}%S) | "
            f"Clarity={bias.clarity} | Decision={result['summary']['decision']}")

        return result

    def _refresh_news_if_due(self, now: datetime):
        """Re-fetch the HIGH-impact calendar every 6 hours."""
        if self._last_news_fetch is not None:
            if (now - self._last_news_fetch).total_seconds() < 6 * 3600:
                return
        try:
            count = fetch_news_events()
            if count >= 0:
                self._last_news_fetch = now
                logger.info(f"News calendar refreshed: {count} HIGH-impact events")
        except Exception as e:
            logger.warning(f"News refresh failed, keeping existing file: {e}")

    def _journal_closed_positions(self, positions: List[Dict], now: datetime):
        """
        Persist finished trades to AdaptiveMemory.

        `AdaptiveMemory.log_trade()` had no callers anywhere in the codebase,
        so `data/trade_journal.db` stayed empty, SEE always voted "no history"
        and the meta-fund never rebalanced — the entire learning loop was
        decorative. This closes it for Pool A by diffing the open-position set
        between cycles and booking whatever disappeared.
        """
        live = {int(p["ticket"]): p for p in positions}

        for ticket, snapshot in list(self._tracked_positions.items()):
            if ticket in live:
                self._tracked_positions[ticket] = snapshot  # still open
                continue

            pnl = self._closed_pnl(ticket)
            entry = float(snapshot.get("open_price", 0.0) or 0.0)
            sl = float(snapshot.get("sl", 0.0) or 0.0)
            risk = abs(entry - sl)
            try:
                self.memory.log_trade(TradeRecord(
                    symbol=snapshot.get("symbol", "UNKNOWN"),
                    direction=snapshot.get("type", "BUY"),
                    model_type=snapshot.get("model_type", "RETURN"),
                    entry_price=entry,
                    exit_price=float(snapshot.get("current_price", 0.0) or 0.0),
                    stop_loss=sl,
                    take_profit=float(snapshot.get("tp", 0.0) or 0.0),
                    lot_size=float(snapshot.get("volume", 0.0) or 0.0),
                    profit_loss=float(pnl),
                    pips=0.0,
                    risk_reward_actual=(float(pnl) / risk) if risk else 0.0,
                    outcome="WIN" if pnl > 0 else ("BREAKEVEN" if pnl == 0 else "LOSS"),
                    regime=snapshot.get("regime", "UNKNOWN"),
                    session=snapshot.get("session", "UNKNOWN"),
                    confidence=float(snapshot.get("confidence", 0.0) or 0.0),
                    clarity=snapshot.get("clarity", "UNKNOWN"),
                    open_time=str(snapshot.get("open_time", "")),
                    close_time=now.isoformat(),
                    notes=f"Pool A ticket={ticket}",
                ))
            except Exception as e:
                logger.warning(f"Journal write failed for ticket {ticket}: {e}")
            self._tracked_positions.pop(ticket, None)

        for ticket, p in live.items():
            self._tracked_positions.setdefault(ticket, p)

    def _closed_pnl(self, ticket: int) -> float:
        """Realised P&L for a closed position: profit + commission + swap."""
        try:
            import MetaTrader5 as mt5
            deals = mt5.history_deals_get(position=ticket)
            if not deals:
                return 0.0
            return sum(getattr(d, "profit", 0.0)
                       + getattr(d, "commission", 0.0)
                       + getattr(d, "swap", 0.0) for d in deals)
        except Exception:
            return 0.0

    def _execute_trade(self, setup: TradeSetup, risk,
                       symbol: str, sym_info: Dict):
        """Send the order via EA."""
        ea_ctx = EAContext(
            setup=setup, risk=risk,
            connector=self.connector, dry_run=self.dry_run)
        order_result = self.ea.execute(ea_ctx)
        if order_result:
            self._trade_count += 1
            logger.info(f"🎯 Trade #{self._trade_count} executed: {symbol} {setup.direction}")


# ══════════════════════════════════════════════════════════════════════════════
# Entry Point
# ══════════════════════════════════════════════════════════════════════════════

def main():
    import subprocess
    import sys
    try:
        subprocess.Popen([sys.executable, "trade_guardian_agent.py"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        logger.warning(f"Failed to start TGA: {e}")

    parser = argparse.ArgumentParser(description="APEX AI Trading System")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run without placing real orders (uses synthetic data)")
    parser.add_argument("--config", default="config.json",
                        help="Path to config file")
    args = parser.parse_args()

    config = load_config(args.config)

    if args.dry_run:
        config["dry_run"] = True

    system = APEXTradingSystem(config, dry_run=config.get("dry_run", False))
    system.start()


if __name__ == "__main__":
    main()
