#pragma once
#include <math.h>
#include <stdint.h>

// Keep report velocity independent of direction. Per-axis clipping makes
// diagonal reports sqrt(2) faster, which changes iOS pointer acceleration.
class MotionSteps {
 public:
  MotionSteps(long dx, long dy, int step)
      : dx_(dx), dy_(dy), count_((long)ceil(hypot((double)dx, (double)dy) / step)) {}

  bool next(int8_t &x, int8_t &y) {
    if (index_ >= count_) return false;
    ++index_;
    long nx = lround((double)dx_ * index_ / count_);
    long ny = lround((double)dy_ * index_ / count_);
    x = (int8_t)(nx - previous_x_);
    y = (int8_t)(ny - previous_y_);
    previous_x_ = nx;
    previous_y_ = ny;
    return true;
  }

 private:
  long dx_, dy_, count_, index_ = 0, previous_x_ = 0, previous_y_ = 0;
};
