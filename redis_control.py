"""
Redis Control Script for BTC Bot Simulation Mode
Toggle between simulation and live trading without restarting
"""

import os
import sys

import redis
from dotenv import load_dotenv

load_dotenv()

# Windows consoles with legacy codepages (cp1252) can't print the emoji
# used below; replace unencodable chars instead of crashing.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:
    pass


def get_redis_client():
    """Get Redis client."""
    try:
        client = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            db=int(os.getenv("REDIS_DB", 2)),
            decode_responses=True,
            socket_connect_timeout=5,
        )
        client.ping()
        return client
    except Exception as e:
        print(f"✗ Redis connection failed: {e}")
        print("  Make sure Redis is running: redis-server")
        return None


def get_current_mode(client):
    """Get current simulation mode."""
    try:
        mode = client.get("btc_trading:simulation_mode")
        if mode is None:
            return None
        return mode == "1"
    except Exception as e:
        print(f"✗ Error reading mode: {e}")
        return None


def set_simulation_mode(client, simulation: bool):
    """Set simulation mode."""
    try:
        client.set("btc_trading:simulation_mode", "1" if simulation else "0")
        mode_text = "SIMULATION" if simulation else "LIVE TRADING"
        print(f"✓ Mode set to: {mode_text}")
        return True
    except Exception as e:
        print(f"✗ Error setting mode: {e}")
        return False


def get_active_strategy(client):
    """Get active strategy ('fusion' or 'tradingview'). Defaults to 'fusion'."""
    try:
        value = client.get("btc_trading:active_strategy")
        if value in ("fusion", "tradingview"):
            return value
        return None
    except Exception as e:
        print(f"✗ Error reading strategy: {e}")
        return None


def set_active_strategy(client, strategy: str):
    """Set active strategy."""
    try:
        client.set("btc_trading:active_strategy", strategy)
        print(f"✓ Active strategy set to: {strategy.upper()}")
        return True
    except Exception as e:
        print(f"✗ Error setting strategy: {e}")
        return False


def get_tv_dry_run(client):
    """Get TradingView dry-run flag (True/False)."""
    try:
        return client.get("btc_trading:tv_dry_run") == "1"
    except Exception as e:
        print(f"✗ Error reading dry-run flag: {e}")
        return False


def set_tv_dry_run(client, enabled: bool):
    """Set TradingView dry-run flag."""
    try:
        client.set("btc_trading:tv_dry_run", "1" if enabled else "0")
        print(f"✓ TradingView dry run: {'ON' if enabled else 'OFF'}")
        return True
    except Exception as e:
        print(f"✗ Error setting dry-run flag: {e}")
        return False


def display_status(client):
    """Display current status."""
    mode = get_current_mode(client)
    strategy = get_active_strategy(client)
    tv_dry_run = get_tv_dry_run(client)

    print("\n" + "=" * 60)
    print("BTC BOT - CURRENT STATUS")
    print("=" * 60)

    if mode is None:
        print("Status: ⚪ Not set (using default from .env)")
    elif mode:
        print("Status: 🟡 SIMULATION MODE")
        print("  - No real trades will be placed")
        print("  - Safe for testing")
    else:
        print("Status: 🔴 LIVE TRADING MODE")
        print("  - REAL MONEY AT RISK!")
        print("  - Real orders will be placed")

    if strategy is None:
        print("Strategy: FUSION (default — key absent)")
    elif strategy == "tradingview":
        print("Strategy: 📡 TRADINGVIEW WEBHOOK")
        print("  - Trades triggered only by TradingView alerts")
    else:
        print("Strategy: 🧠 FUSION (internal signal processors)")

    if tv_dry_run:
        print("TV Dry Run: 🔵 ON")
        print("  - Webhook trades run the FULL live order path")
        print("  - Order is built/validated but NEVER submitted")
        print("  - Records go to tv_dry_run_trades.json")

    print("=" * 60 + "\n")


def main():
    """Main control interface."""
    print("\n" + "=" * 60)
    print("BTC BOT - SIMULATION MODE CONTROL")
    print("=" * 60)

    # Connect to Redis
    client = get_redis_client()
    if not client:
        return

    # Show current status
    display_status(client)

    # Parse command
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()

        if command in ["sim", "simulation", "on"]:
            print("Switching to SIMULATION mode...")
            set_simulation_mode(client, True)
            display_status(client)

        elif command in ["live", "off"]:
            print("\n⚠️  WARNING: Switching to LIVE TRADING mode!")
            confirm = input("Type 'yes' to confirm: ")
            if confirm.lower() == "yes":
                set_simulation_mode(client, False)
                display_status(client)
            else:
                print("Cancelled.")

        elif command == "strategy":
            target = sys.argv[2].lower() if len(sys.argv) > 2 else None
            if target == "fusion":
                print("Switching to FUSION strategy...")
                set_active_strategy(client, "fusion")
                display_status(client)
            elif target == "tradingview":
                mode = get_current_mode(client)
                if mode is False:
                    print("\n⚠️  WARNING: Bot is in LIVE TRADING mode!")
                    print("TradingView alerts will trigger REAL trades.")
                    confirm = input("Type 'yes' to confirm: ")
                    if confirm.lower() != "yes":
                        print("Cancelled.")
                        return
                print("Switching to TRADINGVIEW strategy...")
                set_active_strategy(client, "tradingview")
                display_status(client)
            else:
                print("Usage: python redis_control.py strategy [fusion|tradingview]")

        elif command == "dryrun":
            target = sys.argv[2].lower() if len(sys.argv) > 2 else None
            if target in ["on", "1"]:
                set_tv_dry_run(client, True)
                display_status(client)
            elif target in ["off", "0"]:
                set_tv_dry_run(client, False)
                display_status(client)
            else:
                print("Usage: python redis_control.py dryrun [on|off]")

        elif command in ["status", "check"]:
            # Already displayed above
            pass

        else:
            print(f"Unknown command: {command}")
            print("\nUsage:")
            print("  python redis_control.py sim       - Enable simulation mode")
            print("  python redis_control.py live      - Enable live trading")
            print("  python redis_control.py status    - Show current status")
            print("  python redis_control.py strategy [fusion|tradingview]")
            print("                                    - Switch active strategy")
            print("  python redis_control.py dryrun [on|off]")
            print(
                "                                    - TradingView dry run (live path,"
            )
            print(
                "                                      order built but not submitted)"
            )
    else:
        # Interactive mode
        print("Commands:")
        print("  1. Enable simulation mode")
        print("  2. Enable live trading (⚠️ DANGEROUS!)")
        print("  3. Check status")
        print("  4. Exit")

        while True:
            try:
                choice = input("\nEnter choice (1-4): ").strip()

                if choice == "1":
                    set_simulation_mode(client, True)
                    display_status(client)

                elif choice == "2":
                    print("\n⚠️  WARNING: This will enable LIVE TRADING!")
                    confirm = input("Type 'yes' to confirm: ")
                    if confirm.lower() == "yes":
                        set_simulation_mode(client, False)
                        display_status(client)
                    else:
                        print("Cancelled.")

                elif choice == "3":
                    display_status(client)

                elif choice == "4":
                    print("Goodbye!")
                    break

                else:
                    print("Invalid choice!")

            except KeyboardInterrupt:
                print("\nGoodbye!")
                break


if __name__ == "__main__":
    main()
