import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("export-grafana-cloud.sh")


def scope(expr, deployment="mainnet"):
    definition = re.search(r"^def scope_exprs.*?^  \);$", SCRIPT.read_text(), re.S | re.M).group(0)
    proc = subprocess.run(
        ["jq", "--raw-output", f'{definition} scope_exprs("{deployment}") | .expr'],
        input=json.dumps({"expr": expr}),
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def export_routes(routes, deployment="mainnet"):
    program = re.search(
        r"""^jq --arg deployment "\$DEPLOYMENT" '([^']*)' "\$document" >"notification-policies/\$DEPLOYMENT-routes\.json"$""",
        SCRIPT.read_text(),
        re.M,
    ).group(1)
    proc = subprocess.run(
        ["jq", "--arg", "deployment", deployment, program],
        input=json.dumps({"policies": [{"routes": routes}]}),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def prints_merge_reminder(exported_routes):
    condition = re.search(r"^if jq --exit-status '([^']*)' \\$", SCRIPT.read_text(), re.M).group(1)
    proc = subprocess.run(
        ["jq", "--exit-status", condition],
        input=json.dumps(exported_routes),
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


# Same routes as own_policy_tree_adds_a_grouped_health_route_for_slack_and_telegram expects.
MUTE_ROUTE = {
    "receiver": "microscope-noop",
    "object_matchers": [["channel", "=", "none"]],
    "mute_time_intervals": ["microscope-always"],
    "continue": False,
}


def health_route(channel, receiver):
    return {
        "receiver": receiver,
        "object_matchers": [["channel", "=", channel], ["signal_kind", "=", "datasource"]],
        "group_by": ["deployment", "signal_kind"],
        "group_wait": "60s",
        "group_interval": "5m",
        "repeat_interval": "4h",
        "continue": False,
    }


@unittest.skipIf(shutil.which("jq") is None, "export-grafana-cloud.sh needs jq")
class ScopeExprs(unittest.TestCase):
    def test_merges_the_deployment_into_existing_matchers(self):
        self.assertEqual(
            scope('max(microscope_rpc_active_endpoint{role="fallback"}) or on() vector(0)'),
            'max(microscope_rpc_active_endpoint{deployment="mainnet", role="fallback"}) or on() vector(0)',
        )

    def test_adds_a_selector_to_bare_series(self):
        self.assertEqual(
            scope("sum(increase(microscope_transactions_total[10m])) > 0 unless sum(increase(loki_write_sent_entries_total[10m])) > 0"),
            'sum(increase(microscope_transactions_total{deployment="mainnet"}[10m])) > 0 unless sum(increase(loki_write_sent_entries_total{deployment="mainnet"}[10m])) > 0',
        )

    def test_scopes_the_log_stream_selector(self):
        self.assertEqual(
            scope('sum(count_over_time({service_name="microscope-indexer"} |= "x" [60s]))'),
            'sum(count_over_time({service_name="microscope-indexer", deployment="mainnet"} |= "x" [60s]))',
        )


@unittest.skipIf(shutil.which("jq") is None, "export-grafana-cloud.sh needs jq")
class Routes(unittest.TestCase):
    def test_exports_the_health_routes_only_with_own_policy_tree(self):
        mute = {
            "receiver": "mainnet-noop",
            "object_matchers": [["channel", "=", "none"], ["deployment", "=", "mainnet"]],
            "mute_time_intervals": ["microscope-always"],
            "continue": False,
        }
        self.assertEqual(export_routes([MUTE_ROUTE]), [mute])

        exported = export_routes([
            MUTE_ROUTE,
            health_route("slack", "microscope-slack"),
            health_route("telegram", "microscope-telegram"),
        ])
        self.assertEqual(exported, [
            mute,
            {
                **health_route("slack", "mainnet-slack"),
                "object_matchers": [
                    ["channel", "=", "slack"],
                    ["signal_kind", "=", "datasource"],
                    ["deployment", "=", "mainnet"],
                ],
            },
            {
                **health_route("telegram", "mainnet-telegram"),
                "object_matchers": [
                    ["channel", "=", "telegram"],
                    ["signal_kind", "=", "datasource"],
                    ["deployment", "=", "mainnet"],
                ],
            },
        ])

    def test_reminds_to_merge_the_routes_only_when_they_hold_a_health_route(self):
        self.assertFalse(prints_merge_reminder(export_routes([MUTE_ROUTE])))
        self.assertTrue(prints_merge_reminder(export_routes([
            MUTE_ROUTE,
            health_route("telegram", "microscope-telegram"),
        ])))


if __name__ == "__main__":
    unittest.main()
