"""Exercise washer YAML's routing and clearing without sending notifications.

Run with PyYAML and Jinja2 installed: python -m unittest discover -s tests
"""
import pathlib
from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import yaml
from jinja2.nativetypes import NativeEnvironment

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / 'kubernetes/apps/home/home-assistant/app/packages/washer.yaml'
LEE = 'notify.mobile_app_leeward_pixel_9_pro_xl'
AUDREY = 'notify.mobile_app_aud_pixel'


class WasherRecipients(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package = yaml.safe_load(PACKAGE.read_text())
        cls.automations = {a['id']: a for a in cls.package['automation']}
        cls.env = NativeEnvironment()

    def render(self, value, variables):
        if isinstance(value, str):
            return self.env.from_string(value).render(**variables)
        if isinstance(value, dict):
            return {k: self.render(v, variables) for k, v in value.items()}
        if isinstance(value, list):
            return [self.render(v, variables) for v in value]
        return value

    def run_actions(self, actions, variables, notify):
        for action in actions:
            if 'if' in action:
                if all(self.render(c['value_template'], variables) for c in action['if']):
                    self.run_actions(action['then'], variables, notify)
            elif action.get('action') == 'script.washer_notify':
                values = self.render(action['data'], variables)
                self.run_actions(self.package['script']['washer_notify']['sequence'], values, notify)
            elif action.get('action', '').startswith('notify.'):
                notify(action['action'], self.render(action['data'], variables))

    def test_all_reminder_stages(self):
        actions = self.automations['washer_reminders']['actions']
        self.assertEqual(actions[0]['variables']['stages'], [1, 2, 4, 8, 12])
        for stage, hour in enumerate([1, 2, 4, 8, 12], 1):
            with self.subTest(hour=hour):
                notify = Mock()
                self.run_actions(actions, {'stage': stage, 'stages': [1, 2, 4, 8, 12]}, notify)
                self.assertEqual([c.args[0] for c in notify.call_args_list], [LEE] + ([AUDREY] if hour >= 4 else []))
                for call in notify.call_args_list:
                    self.assertEqual(call.args[1]['data']['tag'], 'washer_needs_dryer')
                    self.assertEqual(call.args[1]['data']['actions'][0]['action'], 'WASHER_MOVED_TO_DRYER')

    def test_pending_load_cannot_be_restamped_by_another_run(self):
        conditions = self.automations['washer_finished']['conditions']
        self.assertIn({'condition': 'state', 'entity_id': 'input_boolean.needs_dryer', 'state': 'off'}, conditions)

    def test_short_running_period_does_not_count_the_off_delay_as_a_wash(self):
        condition = self.automations['washer_finished']['conditions'][1]['value_template']
        now = datetime(2026, 1, 1, 12)
        for minutes, expected in [(19, False), (20, True)]:
            trigger = SimpleNamespace(from_state=SimpleNamespace(last_changed=now - timedelta(minutes=minutes)))
            with self.subTest(minutes=minutes):
                self.assertEqual(self.render(condition, {'now': lambda: now, 'trigger': trigger}), expected)

    def test_clearing_both_tags_on_both_phones(self):
        for automation, tag in [('washer_cleared', 'washer_needs_dryer'), ('washer_notification_action', 'washer_needs_dryer_test')]:
            with self.subTest(automation=automation):
                notify = Mock()
                actions = self.automations[automation]['actions']
                self.run_actions(actions, {}, notify)
                self.assertEqual([c.args[0] for c in notify.call_args_list], [LEE, AUDREY])
                for call in notify.call_args_list:
                    self.assertEqual(call.args[1], {'message': 'clear_notification', 'data': {'tag': tag}})
        self.assertEqual(self.automations['washer_notification_action']['actions'][0]['action'], 'input_boolean.turn_off')
        self.assertEqual(self.automations['washer_notification_action']['actions'][0]['target']['entity_id'], 'input_boolean.needs_dryer')


if __name__ == '__main__':
    unittest.main()
