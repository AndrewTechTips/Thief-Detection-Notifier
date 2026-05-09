<div align="center">

  <h1>🚨 Thief Detection Notifier</h1>

  <p>
    A real-time <strong>motion detection security system</strong> built with Python and OpenCV.<br />
    When an intruder is detected, the system captures the best frame of the event
    and sends it straight to your inbox — automatically.
  </p>

  <p>
    <img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python" />
    <img src="https://img.shields.io/badge/OpenCV-5C3EE8?style=for-the-badge&logo=opencv&logoColor=white" alt="OpenCV" />
    <img src="https://img.shields.io/badge/Threading-✓-brightgreen?style=for-the-badge" alt="Threading" />
    <img src="https://img.shields.io/badge/SMTP-Gmail-D14836?style=for-the-badge&logo=gmail&logoColor=white" alt="Gmail SMTP" />
  </p>

</div>

<br />

---

## ✨ How It Works

1. The webcam captures a live feed and establishes a **static background frame**
2. Each new frame is compared to the background — significant pixel differences signal **motion**
3. While motion is active, frames are stored **in RAM** (not disk) to avoid SSD wear
4. The moment motion **stops**, the middle frame is picked as the clearest shot of the intruder
5. That single image is saved to disk and **emailed as an attachment** on a background thread
6. The image is **deleted from disk** after the email is sent

---

## 🧠 Under the Hood

### Motion Detection Pipeline
Each frame goes through a preprocessing chain before comparison — grayscale → blur → diff → threshold → dilate → contours:

```python
gray_frame_gau = cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (21, 21), 0)
delta_frame = cv2.absdiff(first_frame, gray_frame_gau)
thresh_frame = cv2.threshold(delta_frame, 60, 255, cv2.THRESH_BINARY)[1]
dil_frame = cv2.dilate(thresh_frame, None, iterations=2)
contours, _ = cv2.findContours(dil_frame, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
```

### Trigger on Motion Stop
The alert fires only when motion **ends** — tracked via a 2-element status buffer. This avoids spamming emails during continuous movement:

```python
status_list = status_list[-2:]  # keep only last 2 states

if status_list[0] == 1 and status_list[1] == 0:  # motion just stopped
    best_frame = motion_frames[len(motion_frames) // 2]  # pick the middle frame
    cv2.imwrite(image_path, best_frame)
    Thread(target=send_email, args=(image_path,)).start()
```

### Non-Blocking Email
The email is sent on a **daemon thread** so the camera feed never freezes while the SMTP connection is open:

```python
email_thread = Thread(target=send_email, args=(image_path,))
email_thread.daemon = True
email_thread.start()
```

---

## 📁 Project Structure

```
Thief-Detection-Notifier/
├── images/          # Temporary intruder snapshots (auto-deleted after email)
├── main.py          # Motion detection loop & OpenCV pipeline
├── emailing.py      # Email builder & Gmail SMTP sender
├── requirements.txt
└── README.md
```

---

## 🚀 Getting Started

1. **Clone the repository:**
    ```bash
    git clone https://github.com/AndrewTechTips/Thief-Detection-Notifier.git
    cd Thief-Detection-Notifier
    ```

2. **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

3. **Set your credentials as environment variables:**
    ```bash
    export EMAIL="your@gmail.com"
    export PASSWORD="your_gmail_app_password"
    ```
    > ⚠️ Use a [Gmail App Password](https://myaccount.google.com/apppasswords), not your real account password.

4. **Run the system:**
    ```bash
    python main.py
    ```
    Press **`q`** to stop the camera feed safely.

> 💡 **Tip:** Adjust `MIN_CONTOUR_AREA` in `main.py` if the camera is further away — lower values increase sensitivity.

---

## 📬 Contact

* **LinkedIn:** [Andrei Condrea](https://www.linkedin.com/in/andrei-condrea-b32148346)
* **Email:** condrea.andrey777@gmail.com

<p align="center">
  <i>"It won't stop them — but they'll know you know." 🎯</i>
</p>