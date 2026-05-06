import cv2
import time

video = cv2.VideoCapture(0)
# We give 1 second for the camera to load and then execute the while true
time.sleep(1)

first_frame = None
while True:
    check, frame = video.read()

    # Preprocessing the frame
    gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray_frame_gau = cv2.GaussianBlur(gray_frame, (21, 21), 0)

    if first_frame is None:
        first_frame = gray_frame_gau

    delta_frame = cv2.absdiff(first_frame, gray_frame_gau)

    thresh_frame = cv2.threshold(delta_frame, 60, 255, cv2.THRESH_BINARY)[1]
    dil_frame = cv2.dilate(thresh_frame, None, iterations=2)

    cv2.imshow("My video", dil_frame)

    contours, check = cv2.findContours(
        dil_frame, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    for contour in contours:
        if cv2.contourArea(contour) < 5000:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 3)

    cv2.imshow("Video", frame)

    # Here we create a keyboard key
    key = cv2.waitKey(1)

    # Then if the user is pressing q on the frame, the code will break out of the loop and then release the video
    if key == ord("q"):
        break

video.release()
