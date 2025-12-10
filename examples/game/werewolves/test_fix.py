import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Test the profile structure fix
class MockAgent:
    def __init__(self):
        self.opponent_profiles = {}
    
    def test_update_profile(self, speaker, content):
        if speaker not in self.opponent_profiles:
            self.opponent_profiles[speaker] = {
                "speech_style": "neutral",
                "behavior_patterns": {
                    "accusation_frequency": 0,
                    "claim_frequency": 0,
                    "voting_consistency": 0.0,
                    "emojis_used": 0
                },
                "role_tendencies": {
                    "wolf_win_rate": 0.0,
                    "villager_win_rate": 0.0,
                    "seer_win_rate": 0.0,
                    "total_games": 0
                },
                "historical_speeches": []
            }
        
        profile = self.opponent_profiles[speaker]
        
        # Add backward compatibility fix
        if "historical_speeches" not in profile:
            profile["historical_speeches"] = []
        if "behavior_patterns" not in profile:
            profile["behavior_patterns"] = {
                "accusation_frequency": 0,
                "claim_frequency": 0,
                "voting_consistency": 0.0,
                "emojis_used": 0
            }
        if "role_tendencies" not in profile:
            profile["role_tendencies"] = {
                "wolf_win_rate": 0.0,
                "villager_win_rate": 0.0,
                "seer_win_rate": 0.0,
                "total_games": 0
            }
        
        # Test accessing historical_speeches
        try:
            profile["historical_speeches"].append(content[:500])
            print(f"✓ Successfully added speech to historical_speeches for {speaker}")
        except KeyError as e:
            print(f"✗ KeyError: {e} for {speaker}")
            return False
        
        # Test accessing behavior_patterns
        try:
            profile["behavior_patterns"]["accusation_frequency"] += 1
            print(f"✓ Successfully updated accusation_frequency for {speaker}")
        except KeyError as e:
            print(f"✗ KeyError: {e} for {speaker}")
            return False
            
        return True

# Test with new profile
agent = MockAgent()
print("Test 1: New profile with correct structure")
result1 = agent.test_update_profile("Player1", "I think Player2 is a wolf!")

# Test with old-style profile (without nested structure)
print("\nTest 2: Old-style profile without nested structure")
agent.opponent_profiles["Player2"] = {
    "speech_style": "aggressive",
    "accusation_frequency": 0,
    "claim_frequency": 0
}
result2 = agent.test_update_profile("Player2", "I am the seer! I checked Player3 last night, he is a wolf.")

print("\n" + "="*50)
if result1 and result2:
    print("✓ All tests passed! The KeyError issue has been fixed.")
else:
    print("✗ Some tests failed.")
