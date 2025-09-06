import os

def main() -> None:
    key_present = bool(os.environ.get("OPENAI_API_KEY"))
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    if key_present:
        print("OPENAI_API_KEY: OK (set)")
    else:
        print("OPENAI_API_KEY: MISSING")
    print(f"OPENAI_MODEL: {model}")

if __name__ == "__main__":
    main()

