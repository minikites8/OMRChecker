"""Align photographed OMR sheets from their printed outer border."""

import math

import cv2
import numpy as np

from src.logger import logger
from src.processors.interfaces.ImagePreprocessor import ImagePreprocessor
from src.utils.interaction import InteractionUtils


class AlignPageBorder(ImagePreprocessor):
    """Warp a photographed sheet so its printed frame matches template pixels."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        options = self.options
        self.target_rect = [int(value) for value in options["targetRect"]]
        self.top_search = options.get("topSearch", [0.055, 0.18])
        self.bottom_search = options.get("bottomSearch", [0.65, 0.94])
        self.left_search = options.get("leftSearch", [0.035, 0.30])
        self.right_search = options.get("rightSearch", [0.70, 0.995])
        self.blur_kernel = tuple(int(value) for value in options.get("blurKernel", [5, 5]))
        self.canny_thresholds = tuple(
            int(value) for value in options.get("cannyThresholds", [50, 150])
        )
        self.hough_threshold = int(options.get("houghThreshold", 250))
        self.theta_divisor = int(options.get("thetaDivisor", 1800))
        self.angle_tolerance = float(options.get("angleTolerance", 8))
        self.max_bottom_line_rank = int(options.get("maxBottomLineRank", 500))
        self.min_area_ratio = float(options.get("minAreaRatio", 0.25))
        self.disable_auto_align = bool(options.get("disableAutoAlign", True))
        if self.disable_auto_align:
            self.tuning_config.alignment_params.auto_align = False
        self.description = "Align photographed page using its printed outer border"

    @staticmethod
    def _line_y(line, x):
        rho, theta = line
        sine = math.sin(theta)
        if abs(sine) < 1e-6:
            return None
        return (rho - x * math.cos(theta)) / sine

    @staticmethod
    def _line_x(line, y):
        rho, theta = line
        cosine = math.cos(theta)
        if abs(cosine) < 1e-6:
            return None
        return (rho - y * math.sin(theta)) / cosine

    @staticmethod
    def _intersection(first, second):
        first_coefficients = np.array(
            [math.cos(first[1]), math.sin(first[1]), -first[0]], dtype=float
        )
        second_coefficients = np.array(
            [math.cos(second[1]), math.sin(second[1]), -second[0]], dtype=float
        )
        point = np.cross(first_coefficients, second_coefficients)
        if abs(point[2]) < 1e-6:
            return None
        return point[:2] / point[2]

    @staticmethod
    def _within(value, normalized_range, dimension):
        return normalized_range[0] * dimension <= value <= normalized_range[1] * dimension

    def _classify_lines(self, lines, width, height):
        horizontal = []
        vertical = []
        for rank, (rho, theta) in enumerate(np.asarray(lines).reshape(-1, 2)):
            line = (float(rho), float(theta))
            angle = math.degrees(theta)
            if abs(angle - 90) <= self.angle_tolerance:
                position = self._line_y(line, width / 2)
                if position is not None:
                    horizontal.append((rank, position, line))
            if angle <= self.angle_tolerance or angle >= 180 - self.angle_tolerance:
                position = self._line_x(line, height / 2)
                if position is not None:
                    vertical.append((rank, position, line))
        return horizontal, vertical

    def find_border(self, image):
        height, width = image.shape[:2]
        normalized = cv2.normalize(image, None, 0, 255, cv2.NORM_MINMAX)
        blurred = cv2.GaussianBlur(normalized, self.blur_kernel, 0)
        edges = cv2.Canny(blurred, *self.canny_thresholds)
        lines = cv2.HoughLines(
            edges,
            1,
            np.pi / self.theta_divisor,
            self.hough_threshold,
        )
        if lines is None:
            return None, edges

        horizontal, vertical = self._classify_lines(lines, width, height)
        top_candidates = [
            item
            for item in horizontal
            if self._within(item[1], self.top_search, height)
        ]
        bottom_candidates = [
            item
            for item in horizontal
            if item[0] < self.max_bottom_line_rank
            and self._within(item[1], self.bottom_search, height)
        ]
        left_candidates = [
            item
            for item in vertical
            if self._within(item[1], self.left_search, width)
        ]
        right_candidates = [
            item
            for item in vertical
            if self._within(item[1], self.right_search, width)
        ]
        if not all(
            [top_candidates, bottom_candidates, left_candidates, right_candidates]
        ):
            return None, edges

        top = min(top_candidates, key=lambda item: item[0])[2]
        bottom = max(bottom_candidates, key=lambda item: item[1])[2]
        left = min(left_candidates, key=lambda item: item[0])[2]
        right = min(right_candidates, key=lambda item: item[0])[2]

        corners = [
            self._intersection(top, left),
            self._intersection(top, right),
            self._intersection(bottom, right),
            self._intersection(bottom, left),
        ]
        if any(point is None for point in corners):
            return None, edges

        corners = np.asarray(corners, dtype=np.float32)
        if not np.isfinite(corners).all():
            return None, edges

        area_ratio = abs(cv2.contourArea(corners)) / float(width * height)
        if area_ratio < self.min_area_ratio:
            logger.error(
                "Detected page border is too small: "
                f"area ratio {round(area_ratio, 3)} < {self.min_area_ratio}"
            )
            return None, edges
        return corners, edges

    def apply_filter(self, image, file_path):
        corners, edges = self.find_border(image)
        if corners is None:
            logger.error(f"Printed page border not found for: '{file_path}'")
            return None

        height, width = image.shape[:2]
        left, top, right, bottom = self.target_rect
        if not (0 <= left < right < width and 0 <= top < bottom < height):
            logger.error(
                f"targetRect {self.target_rect} exceeds image dimensions "
                f"{[width, height]}"
            )
            return None

        destination = np.asarray(
            [[left, top], [right, top], [right, bottom], [left, bottom]],
            dtype=np.float32,
        )
        transform = cv2.getPerspectiveTransform(corners, destination)
        border_value = 255 if image.ndim == 2 else (255, 255, 255)
        aligned = cv2.warpPerspective(
            image,
            transform,
            (width, height),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=border_value,
        )

        logger.info(
            "Aligned printed page border: "
            f"{np.round(corners, 1).tolist()} -> {destination.tolist()}"
        )
        if self.tuning_config.outputs.show_image_level >= 4:
            preview = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
            cv2.polylines(preview, [corners.astype(np.int32)], True, (0, 0, 255), 4)
            InteractionUtils.show(
                f"Page border: {file_path}", preview, resize=True, config=self.tuning_config
            )
            InteractionUtils.show(
                f"Aligned page: {file_path}", aligned, resize=True, config=self.tuning_config
            )
        return aligned
