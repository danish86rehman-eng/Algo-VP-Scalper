"""
APEX AI — Terminal Dashboard
Rich-powered live monitoring interface showing all system state.
"""
from __future__ import annotations
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
import sys

from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.live import Live
from rich.align import Align
from rich.columns import Columns
from rich import box
from rich.rule import Rule
from rich.progress import BarColumn, Progress, TextColumn

console = Console()

# ── Color palette ─────────────────────────────────────────────────────────────
C_GOLD    = "bright_yellow"
C_GREEN   = "bright_green"
C_RED     = "bright_red"
C_BLUE    = "bright_cyan"
C_ORANGE  = "dark_orange"
C_GRAY    = "grey70"
C_WHITE   = "bold white"
C_PURPLE  = "medium_purple"
C_DIM     = "grey50"


def _trend_color(trend: str) -> str:
    return C_GREEN if trend == "BULLISH" else (C_RED if trend == "BEARISH" else C_GRAY)


def _vote_color(vote: str) -> str:
    return C_GREEN if vote == "BULLISH" else (C_RED if vote == "BEARISH" else
           C_GRAY if vote in ("NEUTRAL", "ABSTAIN") else C_GRAY)


def _regime_color(regime: str) -> str:
    return {
        "EXPANSION": C_GREEN, "MANIPULATION": C_ORANGE,
        "ROTATION": C_BLUE, "TRANSITION": C_PURPLE
    }.get(regime, C_GRAY)


def _behavior_color(state: str) -> str:
    return {
        "EXPLOIT": C_GREEN, "OBSERVE": C_BLUE,
        "ADAPT": C_ORANGE, "REDUCE": C_ORANGE,
        "DISENGAGE": C_RED
    }.get(state, C_GRAY)


def _gov_color(mode: str) -> str:
    return {
        "AGGRESSIVE": C_RED, "BALANCED": C_GREEN,
        "DEFENSIVE": C_ORANGE, "PRESERVATION": C_PURPLE
    }.get(mode, C_GRAY)


class TerminalDashboard:
    """
    APEX AI Terminal Dashboard — institutional-grade live monitoring.
    """

    def __init__(self):
        self._live: Optional[Live] = None
        self._state: Dict[str, Any] = {}

    def render(self, state: Dict[str, Any]) -> Layout:
        """Build and return the full dashboard layout."""
        self._state = state
        layout = Layout()
        layout.split_column(
            Layout(self._header(), size=4),
            Layout(name="body"),
            Layout(self._footer(), size=3),
        )
        layout["body"].split_row(
            Layout(name="left",   ratio=3),
            Layout(name="right",  ratio=2),
        )
        layout["left"].split_column(
            Layout(self._market_analysis_panel(), ratio=3),
            Layout(self._agent_consensus_panel(), ratio=2),
        )
        layout["right"].split_column(
            Layout(self._account_panel(), ratio=2),
            Layout(self._positions_panel(), ratio=2),
            Layout(self._meta_fund_panel(), ratio=1),
        )
        return layout

    # ── Panels ────────────────────────────────────────────────────────────

    def _header(self):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        session = self._state.get("session", {})
        sess_name  = session.get("active_session", "OFF")
        sess_color = C_GREEN if session.get("is_kill_zone") else C_GRAY

        gov_mode  = self._state.get("governance_mode", "BALANCED")
        sys_state = self._state.get("system_state", "OBSERVE")

        header_text = Text(justify="center")
        header_text.append("⚡ APEX AI ", style=f"bold {C_GOLD}")
        header_text.append("INSTITUTIONAL TRADING SYSTEM  ", style=f"bold {C_WHITE}")
        header_text.append(f"│  {now}  │  ", style=C_DIM)
        header_text.append(f"Session: {sess_name}  ", style=f"bold {sess_color}")
        header_text.append(f"│  Gov: {gov_mode}  ", style=f"bold {_gov_color(gov_mode)}")
        header_text.append(f"│  State: {sys_state}", style=f"bold {_behavior_color(sys_state)}")

        return Panel(Align.center(header_text), style=C_GOLD, height=4)

    def _market_analysis_panel(self):
        analyses = self._state.get("analyses", {})
        table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style=f"bold {C_GOLD}",
                      expand=True)
        table.add_column("Symbol",   style="bold white",  width=10)
        table.add_column("Price",    style=C_WHITE,        width=12)
        table.add_column("Regime",   width=16)
        table.add_column("Bias",     width=18)
        table.add_column("Bull%",   justify="right", width=7)
        table.add_column("Bear%",   justify="right", width=7)
        table.add_column("Structure", width=10)
        table.add_column("Session×",  width=8)
        table.add_column("Decision",  width=12)

        for symbol, data in analyses.items():
            regime      = data.get("regime", "UNKNOWN")
            bias        = data.get("dominant", "RANGING")
            bull_pct    = data.get("bull_pct", 0)
            bear_pct    = data.get("bear_pct", 0)
            structure   = data.get("structure_trend", "RANGING")
            price       = data.get("price", 0.0)
            sess_weight = data.get("session_weight", 1.0)
            decision    = data.get("decision", "WAIT")
            clarity     = data.get("clarity", "LOW")

            dec_color = C_GREEN if decision == "EXECUTE" else (C_ORANGE if decision == "WAIT" else C_GRAY)
            table.add_row(
                symbol,
                f"{price:,.4f}",
                Text(regime, style=_regime_color(regime)),
                Text(bias,   style=_trend_color(bias)),
                Text(f"{bull_pct:.0f}%", style=C_GREEN if bull_pct > bear_pct else C_DIM),
                Text(f"{bear_pct:.0f}%", style=C_RED   if bear_pct > bull_pct else C_DIM),
                Text(structure, style=_trend_color(structure)),
                f"×{sess_weight:.1f}",
                Text(decision,  style=dec_color),
            )

        return Panel(table, title=f"[bold {C_GOLD}]📊 MARKET ANALYSIS[/]",
                     border_style=C_GOLD)

    def _agent_consensus_panel(self):
        votes = self._state.get("agent_votes", {})
        consensus = self._state.get("consensus_score", 0.0)

        table = Table(box=box.SIMPLE, show_header=True, header_style=f"bold {C_BLUE}",
                      expand=True)
        table.add_column("Agent", style="bold white", width=8)
        table.add_column("Full Name", width=30)
        table.add_column("Vote",  width=10)
        table.add_column("Conf",  justify="right", width=7)
        table.add_column("Reasoning", width=40)

        agent_names = {
            "LIA": "Liquidity Intelligence Agent",
            "MSA": "Market Structure Agent",
            "RDA": "Regime Detection Agent",
            "CAIA": "Cross-Asset Intel Agent",
            "SEE": "Strategy Evolution Engine",
            "EA":  "Execution Agent",
            "CRG": "Capital Risk Governor",
        }

        for agent, vote_data in votes.items():
            vote      = vote_data.get("vote", "NEUTRAL")
            conf      = vote_data.get("confidence", 0.0)
            reasoning = vote_data.get("reasoning", "")[:38]
            icon = "🟢" if vote == "BULLISH" else ("🔴" if vote == "BEARISH" else
                   "⛔" if vote == "ABSTAIN" else "⚪")
            table.add_row(
                agent, agent_names.get(agent, agent),
                Text(f"{icon} {vote}", style=_vote_color(vote)),
                f"{conf:.0%}",
                Text(reasoning, style=C_DIM)
            )

        # Consensus bar
        bar = "█" * int(consensus * 20) + "░" * (20 - int(consensus * 20))
        consensus_color = C_GREEN if consensus >= 0.71 else (C_ORANGE if consensus >= 0.5 else C_RED)

        title = (f"[bold {C_BLUE}]🤖 AGENT COUNCIL  "
                 f"[{consensus_color}]Consensus: {bar} {consensus:.0%}[/][/]")
        return Panel(table, title=title, border_style=C_BLUE)

    def _account_panel(self):
        acct = self._state.get("account", {})
        compound = self._state.get("compounding", {})
        balance   = acct.get("balance", 0.0)
        equity    = acct.get("equity", 0.0)
        profit    = acct.get("profit", 0.0)
        dd_pct    = compound.get("current_drawdown_pct", 0.0)
        growth    = compound.get("growth_pct", 0.0)
        daily_pnl = compound.get("daily_pnl", 0.0)

        pnl_color  = C_GREEN if profit >= 0 else C_RED
        dd_color   = C_GREEN if dd_pct < 3 else (C_ORANGE if dd_pct < 6 else C_RED)
        grow_color = C_GREEN if growth >= 0 else C_RED

        table = Table(box=box.SIMPLE, show_header=False, expand=True)
        table.add_column("Key",   style=C_GRAY,  width=18)
        table.add_column("Value", style=C_WHITE, width=20)

        table.add_row("Balance",       f"${balance:,.2f}")
        table.add_row("Equity",        f"${equity:,.2f}")
        table.add_row("Open PnL",      Text(f"${profit:+,.2f}", style=pnl_color))
        table.add_row("Daily PnL",     Text(f"${daily_pnl:+,.2f}", style=C_GREEN if daily_pnl >= 0 else C_RED))
        table.add_row("Total Growth",  Text(f"{growth:+.2f}%", style=grow_color))
        table.add_row("Drawdown",      Text(f"{dd_pct:.2f}%", style=dd_color))
        table.add_row("Max DD",        Text(f"{compound.get('max_drawdown_pct', 0):.2f}%", style=C_RED))
        table.add_row("Suggested Risk",f"{compound.get('suggested_risk_pct', 1.0):.2f}%")

        return Panel(table, title=f"[bold {C_GOLD}]💰 ACCOUNT STATUS[/]", border_style=C_GOLD)

    def _positions_panel(self):
        positions = self._state.get("positions", [])
        table = Table(box=box.SIMPLE, show_header=True, header_style=f"bold {C_WHITE}",
                      expand=True)
        table.add_column("Symbol",    width=10)
        table.add_column("Dir",       width=6)
        table.add_column("Lot",       justify="right", width=7)
        table.add_column("Open",      justify="right", width=10)
        table.add_column("PnL",       justify="right", width=10)

        if not positions:
            table.add_row("[grey50]— No open positions —[/]", "", "", "", "")
        else:
            for p in positions:
                pnl   = p.get("profit", 0.0)
                dir_  = p.get("type", "BUY")
                color = C_GREEN if pnl >= 0 else C_RED
                dir_color = C_GREEN if dir_ == "BUY" else C_RED
                table.add_row(
                    p.get("symbol", ""),
                    Text(dir_, style=dir_color),
                    str(p.get("volume", 0)),
                    f"{p.get('open_price', 0):.4f}",
                    Text(f"${pnl:+.2f}", style=color)
                )

        return Panel(table, title=f"[bold {C_WHITE}]📋 OPEN POSITIONS[/]", border_style=C_GRAY)

    def _meta_fund_panel(self):
        funds = self._state.get("meta_fund", {})
        table = Table(box=box.SIMPLE, show_header=False, expand=True)
        table.add_column("Fund",   width=14)
        table.add_column("Weight", width=8)
        table.add_column("WR",     width=8)
        table.add_column("Status", width=12)

        for name, data in funds.items():
            weight = data.get("capital_weight", 0.33)
            wr     = data.get("win_rate", 0.0)
            status = data.get("status", "ACTIVE")
            s_color = C_GREEN if status == "ACTIVE" else (C_ORANGE if status == "OBSERVATION" else C_RED)
            bar_len = int(weight * 10)
            bar = "█" * bar_len + "░" * (10 - bar_len)
            table.add_row(
                f"[bold]{name}[/]",
                f"[{C_GOLD}]{bar}[/] {weight:.0%}",
                f"{wr:.0%}",
                Text(status, style=s_color)
            )

        return Panel(table, title=f"[bold {C_PURPLE}]🧬 META-FUND (v11)[/]", border_style=C_PURPLE)

    def _footer(self):
        session = self._state.get("session", {})
        next_kz = self._state.get("next_kz", "Unknown")
        total_cycles = self._state.get("cycle_count", 0)
        last_exec  = self._state.get("last_execution", "None")
        sys_uptime = self._state.get("uptime", "0m")

        text = Text(justify="center")
        text.append(f"Next KZ: {next_kz}  │  ", style=C_BLUE)
        text.append(f"Cycle #{total_cycles}  │  ", style=C_DIM)
        text.append(f"Last Exec: {last_exec}  │  ", style=C_DIM)
        text.append(f"Uptime: {sys_uptime}  │  ", style=C_DIM)
        text.append("APEX AI v11 — Capital Allocation Intelligence", style=f"bold {C_GOLD}")
        return Panel(Align.center(text), style=C_DIM, height=3)

    # ── Live context manager ───────────────────────────────────────────────

    def start_live(self) -> Live:
        self._live = Live(console=console, refresh_per_second=1, screen=True)
        return self._live

    def update_live(self, state: Dict[str, Any]):
        if self._live:
            self._live.update(self.render(state))

    def print_startup_banner(self):
        console.print()
        console.rule(f"[bold {C_GOLD}]** APEX AI TRADING SYSTEM -- INITIALIZING **[/]")
        console.print()
        lines = [
            ("System",  "APEX AI -- Institutional Capital Allocation Intelligence"),
            ("Version", "v11 -- Meta-Fund Evolution"),
            ("Engines", "Liquidity | Manipulation | Structure | Displacement | Execution"),
            ("Agents",  "LIA | MSA | RDA | CAIA | SEE | EA | CRG (7 agents)"),
            ("Fund OS", "5-Gate Approval | Simulation | Compounding | Meta-Fund"),
        ]
        for label, value in lines:
            console.print(f"  [{C_GOLD}]{label:12}[/]  {value}", style=C_WHITE)
        console.print()
        console.rule(f"[{C_GOLD}]--[/]")
        console.print()

    def print_cycle_summary(self, symbol: str, decision: str, reasoning: str = ""):
        color = C_GREEN if decision == "EXECUTE" else C_ORANGE
        console.print(f"  [{C_DIM}]{datetime.now(timezone.utc).strftime('%H:%M:%S')}[/]  "
                      f"[bold]{symbol}[/]  [{color}]{decision}[/]  {reasoning}", highlight=False)
