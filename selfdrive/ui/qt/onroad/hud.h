#pragma once

#include <QPainter>
#include "selfdrive/ui/ui.h"

class HudRenderer : public QObject {
  Q_OBJECT

public:
  HudRenderer();
  void updateState(const UIState &s);
  void draw(QPainter &p, const QRect &surface_rect);

private:
  void drawSetSpeed(QPainter &p, const QRect &surface_rect);
  void drawCurrentSpeed(QPainter &p, const QRect &surface_rect);
  void drawText(QPainter &p, int x, int y, const QString &text, int alpha = 255);

  float speed = 0;
  float set_speed = 0;
  bool is_cruise_set = false;
  bool is_cruise_available = true;
  bool is_metric = false;
  bool v_ego_cluster_seen = false;
  int status = STATUS_DISENGAGED;

  // [RADAR_TRACK_TEST_START] - Remove this block when radar track testing is done
  bool radar_tracks_enabled = false;
  int radar_track_count = 0;
  float radar_lead_drel = 0.0f;
  float radar_lead_vrel = 0.0f;
  int radar_lead_track_id = -1;
  // [RADAR_TRACK_TEST_END]

  // [RADAR_TRACK_TEST_START]
  void drawRadarTrackStatus(QPainter &p, const QRect &surface_rect);
  // [RADAR_TRACK_TEST_END]
};
