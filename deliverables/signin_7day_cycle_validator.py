"""
7日签到闭环自动校验脚本

用途：
1. 自动验证连续 7 天正常签到的核心闭环。
2. 自动验证第 7 天完成后，当天不立即开启下一轮，需次日日切后才进入下一轮。

假设：
1. 测试环境提供以下接口：
   - GET  /signin/status
   - POST /signin/claim
   - GET  /signin/records
   - POST /test/advance_day  (或等价的测试时间推进能力)
2. records 接口能返回奖励流水，至少区分 daily / grand 两类 rewardType。
3. 这是逻辑校验脚本模板，字段名若与实际环境不同，可按项目接口做映射调整。
"""

from dataclasses import dataclass
from typing import Dict, Any

import requests


@dataclass
class Config:
    base_url: str = "http://test-env"
    player_id: str = "test_player_001"
    activity_id: int = 1001
    timeout: int = 5


class SignInCycleValidator:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.session = requests.Session()

    def get_status(self) -> Dict[str, Any]:
        response = self.session.get(
            f"{self.config.base_url}/signin/status",
            params={
                "playerId": self.config.player_id,
                "activityId": self.config.activity_id,
            },
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        return response.json()

    def claim(self) -> Dict[str, Any]:
        response = self.session.post(
            f"{self.config.base_url}/signin/claim",
            json={
                "playerId": self.config.player_id,
                "activityId": self.config.activity_id,
            },
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        return response.json()

    def get_records(self) -> Dict[str, Any]:
        response = self.session.get(
            f"{self.config.base_url}/signin/records",
            params={
                "playerId": self.config.player_id,
                "activityId": self.config.activity_id,
            },
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        return response.json()

    def advance_one_day(self) -> None:
        response = self.session.post(
            f"{self.config.base_url}/test/advance_day",
            json={"days": 1},
            timeout=self.config.timeout,
        )
        response.raise_for_status()

    @staticmethod
    def assert_equal(actual: Any, expected: Any, message: str) -> None:
        if actual != expected:
            raise AssertionError(
                f"{message} | expected={expected!r}, actual={actual!r}"
            )

    @staticmethod
    def slot_status_map(status: Dict[str, Any]) -> Dict[int, str]:
        return {
            int(slot["slotIndex"]): slot["status"]
            for slot in status.get("slots", [])
        }

    @staticmethod
    def count_rewards(records: Dict[str, Any], reward_type: str) -> int:
        reward_records = records.get("rewardRecords", [])
        return sum(
            1 for record in reward_records if record.get("rewardType") == reward_type
        )

    def assert_before_claim(self, day: int, status: Dict[str, Any]) -> None:
        slots = self.slot_status_map(status)

        self.assert_equal(
            status["currentRoundNo"], 1, f"Day{day}: currentRoundNo before claim"
        )
        self.assert_equal(
            status["roundStatus"], "in_progress", f"Day{day}: roundStatus before claim"
        )
        self.assert_equal(
            status["todayClaimed"], False, f"Day{day}: todayClaimed before claim"
        )

        for slot_index in range(1, 8):
            if slot_index < day:
                self.assert_equal(
                    slots[slot_index],
                    "claimed",
                    f"Day{day}: slot{slot_index} should already be claimed",
                )
            elif slot_index == day:
                self.assert_equal(
                    slots[slot_index],
                    "claimable",
                    f"Day{day}: slot{slot_index} should be claimable",
                )
            else:
                self.assert_equal(
                    slots[slot_index],
                    "locked",
                    f"Day{day}: slot{slot_index} should still be locked",
                )

    def assert_after_claim(
        self,
        day: int,
        status: Dict[str, Any],
        records_before: Dict[str, Any],
        records_after: Dict[str, Any],
    ) -> None:
        slots = self.slot_status_map(status)

        self.assert_equal(
            slots[day], "claimed", f"Day{day}: slot{day} after claim should be claimed"
        )
        self.assert_equal(
            status["todayClaimed"], True, f"Day{day}: todayClaimed after claim"
        )
        self.assert_equal(
            self.count_rewards(records_after, "daily"),
            self.count_rewards(records_before, "daily") + 1,
            f"Day{day}: daily reward count",
        )

        if day < 7:
            self.assert_equal(
                status["roundStatus"],
                "in_progress",
                f"Day{day}: roundStatus after claim",
            )
            self.assert_equal(
                self.count_rewards(records_after, "grand"),
                0,
                f"Day{day}: grand reward count before day 7",
            )
            return

        self.assert_equal(
            status["roundStatus"], "completed", "Day7: roundStatus after claim"
        )
        self.assert_equal(
            status["grandRewardClaimed"], True, "Day7: grandRewardClaimed"
        )
        self.assert_equal(
            self.count_rewards(records_after, "grand"),
            self.count_rewards(records_before, "grand") + 1,
            "Day7: grand reward count",
        )

    def assert_same_day_completed(self) -> None:
        status = self.get_status()

        self.assert_equal(
            status["currentRoundNo"], 1, "After Day7 same day: still round 1"
        )
        self.assert_equal(
            status["roundStatus"], "completed", "After Day7 same day: round completed"
        )

        has_claimable_slot = any(
            slot["status"] == "claimable" for slot in status.get("slots", [])
        )
        self.assert_equal(
            has_claimable_slot,
            False,
            "After Day7 same day: no new claimable slot should appear",
        )

    def assert_next_round_opened(self) -> None:
        status = self.get_status()
        slots = self.slot_status_map(status)

        self.assert_equal(status["currentRoundNo"], 2, "Next day: should enter round 2")
        self.assert_equal(
            status["roundStatus"], "in_progress", "Next day: round 2 should be in progress"
        )
        self.assert_equal(
            status["todayClaimed"], False, "Next day: todayClaimed should reset"
        )
        self.assert_equal(
            status["grandRewardClaimed"],
            False,
            "Next day: grandRewardClaimed should reset",
        )

        for slot_index in range(1, 8):
            expected = "claimable" if slot_index == 1 else "locked"
            self.assert_equal(
                slots[slot_index],
                expected,
                f"Round2 Day1: slot{slot_index} status",
            )

    def run(self) -> None:
        print("Start validating 7-day sign-in cycle...")

        for day in range(1, 8):
            print(f"[Day {day}] checking pre-claim state")
            status_before = self.get_status()
            records_before = self.get_records()
            self.assert_before_claim(day, status_before)

            print(f"[Day {day}] claiming today's slot")
            claim_response = self.claim()
            self.assert_equal(
                claim_response.get("success"),
                True,
                f"Day{day}: claim response should be successful",
            )

            print(f"[Day {day}] checking post-claim state")
            status_after = self.get_status()
            records_after = self.get_records()
            self.assert_after_claim(day, status_after, records_before, records_after)

            if day < 7:
                print(f"[Day {day}] advancing to next server day")
                self.advance_one_day()

        print("[Day 7] checking same-day completed state")
        self.assert_same_day_completed()

        print("[Next day] advancing after round completion")
        self.advance_one_day()

        print("[Next day] checking next round initialization")
        self.assert_next_round_opened()

        print("Validation passed: 7-day sign-in cycle is closed and consistent.")


def main() -> None:
    validator = SignInCycleValidator(Config())
    validator.run()


if __name__ == "__main__":
    main()
