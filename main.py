import os
import cv2
import time
from emailing import send_email
from threading import Thread

MIN_CONTOUR_AREA = 10000  # Adjust if the camera is further away (e.g., 5000)
GAUSSIAN_BLUR = (21, 21)
THRESHOLD_VALUE = 60
IMAGES_FOLDER = "images"


def main():
    # Ensure the images directory exists
    if not os.path.exists(IMAGES_FOLDER):
        os.makedirs(IMAGES_FOLDER)

    video = cv2.VideoCapture(0)
    time.sleep(2)  # Give the camera 2 seconds to adjust to lighting

    first_frame = None
    status_list = [0, 0]
    event_count = 1
    frame_to_export = None
    max_area = 0

    print("Thief Detection Notifier is ACTIVE. Press 'q' to quit.")

    while True:
        status = 0
        check, frame = video.read()

        if not check:
            print("Failed to grab frame. Exiting...")
            break

        # Preprocessing the frame for motion detection
        gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray_frame_gau = cv2.GaussianBlur(gray_frame, GAUSSIAN_BLUR, 0)

        if first_frame is None:
            first_frame = gray_frame_gau
            continue

        # Calculate difference between the static background and current frame
        delta_frame = cv2.absdiff(first_frame, gray_frame_gau)
        thresh_frame = cv2.threshold(
            delta_frame, THRESHOLD_VALUE, 255, cv2.THRESH_BINARY
        )[1]
        dil_frame = cv2.dilate(thresh_frame, None, iterations=2)

        contours, _ = cv2.findContours(
            dil_frame, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        current_frame_max_area = 0

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < MIN_CONTOUR_AREA:
                continue

            # Draw bounding box around the intruder
            x, y, w, h = cv2.boundingRect(contour)
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 3)
            status = 1

            if area > current_frame_max_area:
                current_frame_max_area = area

        if status == 1 and current_frame_max_area > max_area:
            max_area = current_frame_max_area
            frame_to_export = frame.copy()

        status_list.append(status)
        status_list = status_list[-2:]

        # Trigger action ONLY when motion stops
        if status_list[0] == 1 and status_list[1] == 0:
            if frame_to_export is not None:
                image_path = os.path.join(IMAGES_FOLDER, f"intruder_{event_count}.png")
                cv2.imwrite(image_path, frame_to_export)

                # Start background thread to send email and clean up
                email_thread = Thread(target=send_email, args=(image_path,))
                email_thread.daemon = True
                email_thread.start()

                event_count += 1
                frame_to_export = None  # Reset for the next event
                max_area = 0

        cv2.imshow("Security Camera Feed", frame)

        # Listen for the 'q' key to quit
        if cv2.waitKey(1) == ord("q"):
            print("Turning off camera...")
            break

    # Clean up resources safely
    video.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
