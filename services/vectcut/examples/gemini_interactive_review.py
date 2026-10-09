import os
import sys
import time
from playwright.sync_api import sync_playwright

MASTER_VIDEO = r"c:\Users\Hemanshi Makwana\OneDrive\Documents\new\Hemanshi_Master.mp4"
PROFILE_DIR = r"C:\Users\Hemanshi Makwana\.gemini\playwright_gemini_profile"

REVIEW_PROMPT = """Please conduct a thorough creative and technical director's review of this 25-second vertical video reel explaining the etymology of the name "Hemanshi" (हेमांशी).

Evaluate:
1. Visual Polish & Pacing across all 4 scenes (Hook, Meaning/Etymology, Portrait of Hemanshi, Outro Resolution).
2. Typography & Legibility (contrast, font choice, font scale, safe zones for mobile 9:16).
3. Color Grading & Lighting Harmonization (golden hour warmth, highlight bloom vs direct flash).
4. Subject Framing (Ken Burns motion, background removal).

Please provide specific, actionable suggestions for any further refinement.
"""

def main():
    print("=== PLAYWRIGHT GEMINI REVIEW RUNNER ===", flush=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            viewport=None,
            args=["--start-maximized"]
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://gemini.google.com/app")
        page.wait_for_load_state("domcontentloaded")
        time.sleep(3)

        print("Browser window is active on screen.", flush=True)

        # Check if Sign in button is visible
        sign_in_btns = page.locator('button:has-text("Sign in"), a:has-text("Sign in")')
        if sign_in_btns.count() > 0 and sign_in_btns.first.is_visible():
            print("\n>>> PLEASE SIGN IN TO YOUR GOOGLE ACCOUNT IN THE OPENED BROWSER WINDOW <<<", flush=True)
            print("Waiting for sign-in completion...", flush=True)
            
            # Wait until sign in button disappears
            while True:
                time.sleep(4)
                btns = page.locator('button:has-text("Sign in"), a:has-text("Sign in")')
                if btns.count() == 0 or not btns.first.is_visible():
                    print("Sign-in detected successfully!", flush=True)
                    break

        time.sleep(4)
        print("Logged in! Preparing to upload video...", flush=True)

        # Click the "+" / "Upload & tools" button to open the upload menu
        upload_btn = page.locator('button[aria-label*="Upload"], button[aria-label*="tool"], button:has-text("+")').first
        if upload_btn.is_visible():
            upload_btn.click()
            time.sleep(1)

        # Look for the file chooser trigger (e.g. "Upload files" / "Upload from computer")
        print("Attaching video file: " + MASTER_VIDEO, flush=True)
        try:
            with page.expect_file_chooser(timeout=8000) as fc_info:
                # Find upload option in dropdown
                upload_opt = page.locator('text="Upload files", text="Upload from computer", [role="menuitem"]:has-text("Upload"), [role="menuitem"]:has-text("file")').first
                if upload_opt.is_visible():
                    upload_opt.click()
                else:
                    # fallback to hidden file input if present
                    pass
            file_chooser = fc_info.value
            file_chooser.set_files(MASTER_VIDEO)
            print("Video attached via file chooser!", flush=True)
        except Exception as e:
            print("Notice: looking for direct input[type=file]...", flush=True)
            file_inputs = page.locator('input[type="file"]')
            if file_inputs.count() > 0:
                file_inputs.first.set_input_files(MASTER_VIDEO)
                print("Video attached via direct input[type=file]!", flush=True)
            else:
                print("Could not find file chooser or input[type=file]:", e, flush=True)

        print("Waiting 10 seconds for video upload and processing...", flush=True)
        time.sleep(10)

        # Enter review prompt
        print("Entering review prompt...", flush=True)
        tb = page.locator('div[role="textbox"], rich-textarea p, textarea').first
        tb.click()
        tb.fill(REVIEW_PROMPT)
        time.sleep(2)

        # Click send
        print("Submitting prompt to Gemini...", flush=True)
        send_btn = page.locator('button[aria-label*="Send"], button[aria-label*="Submit"]').first
        send_btn.click()

        print("Prompt sent! Waiting for Gemini critique to generate...", flush=True)
        time.sleep(8)

        # Wait until generation finishes (Stop button disappears)
        start_wait = time.time()
        while time.time() - start_wait < 240:
            stop_btn = page.locator('button[aria-label*="Stop"]')
            if stop_btn.count() == 0 or not stop_btn.first.is_visible():
                print("Gemini response is ready!", flush=True)
                break
            time.sleep(3)

        time.sleep(3)

        # Extract review text
        review_text = ""
        responses = page.locator('.model-response-text, message-content, [data-message-id]')
        if responses.count() > 0:
            review_text = responses.last.inner_text()
        else:
            divs = page.locator('.markdown')
            if divs.count() > 0:
                review_text = divs.last.inner_text()

        print("\n" + "="*50, flush=True)
        print("=== GEMINI REVIEW ===", flush=True)
        print("="*50, flush=True)
        print(review_text, flush=True)
        print("="*50, flush=True)

        # Save to output file
        out_path = r"C:\Users\Hemanshi Makwana\.gemini\antigravity\brain\28d91483-bf68-46d9-9011-69435440cec4\scratch\gemini_review_output.txt"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(review_text)
        print(f"Review successfully saved to {out_path}", flush=True)

        time.sleep(5)
        context.close()

if __name__ == "__main__":
    main()
