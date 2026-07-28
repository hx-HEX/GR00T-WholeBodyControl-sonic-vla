#include <algorithm>
#include <array>
#include <chrono>
#include <csignal>
#include <iostream>
#include <string>
#include <thread>

#include <unitree/idl/go2/MotorCmds_.hpp>
#include <unitree/robot/channel/channel_factory.hpp>
#include <unitree/robot/channel/channel_publisher.hpp>

namespace {

volatile std::sig_atomic_t g_running = 1;

void signal_handler(int) {
  g_running = 0;
}

struct Config {
  std::string iface = "enp6s0";
  std::string topic = "rt/inspire/cmd";
  std::string hand = "both";   // left/right/both
  std::string pose = "open";   // open/close/half/custom
  double max_close = 0.2;       // only used by close
  double duration = 3.0;
  double hz = 50.0;
  std::array<double, 6> custom_right = {1, 1, 1, 1, 1, 1};
  std::array<double, 6> custom_left = {1, 1, 1, 1, 1, 1};
};

void usage(const char* argv0) {
  std::cout
      << "Usage: " << argv0 << " [options]\n"
      << "Options:\n"
      << "  --iface <name>         Unitree DDS network interface, default enp6s0\n"
      << "  --topic <topic>        DDS command topic, default rt/inspire/cmd\n"
      << "  --hand <left|right|both> default both\n"
      << "  --pose <open|close|half|custom> default open\n"
      << "  --max-close <0..1>     For --pose close. 0=open, 1=full close. default 0.2\n"
      << "  --duration <sec>       Publish duration. default 3\n"
      << "  --hz <hz>              Publish rate. default 50\n"
      << "  --right q0,q1,q2,q3,q4,q5  Custom right q, Inspire order\n"
      << "  --left  q0,q1,q2,q3,q4,q5  Custom left q, Inspire order\n"
      << "\n"
      << "Inspire q convention: 1=open, 0=close.\n"
      << "Joint order per hand: pinky, ring, middle, index, thumb_bend, thumb_rotation.\n";
}

bool parse_q6(const std::string& s, std::array<double, 6>& out) {
  size_t start = 0;
  for (size_t i = 0; i < out.size(); ++i) {
    size_t end = s.find(',', start);
    std::string tok = s.substr(start, end == std::string::npos ? std::string::npos : end - start);
    if (tok.empty()) return false;
    out[i] = std::clamp(std::stod(tok), 0.0, 1.0);
    if (i + 1 < out.size()) {
      if (end == std::string::npos) return false;
      start = end + 1;
    } else if (end != std::string::npos) {
      return false;
    }
  }
  return true;
}

bool parse_args(int argc, char** argv, Config& cfg) {
  auto need_value = [&](int& i, const char* name) -> const char* {
    if (i + 1 >= argc) {
      std::cerr << "Missing value for " << name << "\n";
      return nullptr;
    }
    return argv[++i];
  };
  for (int i = 1; i < argc; ++i) {
    std::string a = argv[i];
    if (a == "-h" || a == "--help") {
      usage(argv[0]);
      return false;
    } else if (a == "--iface") {
      const char* v = need_value(i, "--iface"); if (!v) return false; cfg.iface = v;
    } else if (a == "--topic") {
      const char* v = need_value(i, "--topic"); if (!v) return false; cfg.topic = v;
    } else if (a == "--hand") {
      const char* v = need_value(i, "--hand"); if (!v) return false; cfg.hand = v;
    } else if (a == "--pose") {
      const char* v = need_value(i, "--pose"); if (!v) return false; cfg.pose = v;
    } else if (a == "--max-close") {
      const char* v = need_value(i, "--max-close"); if (!v) return false;
      cfg.max_close = std::clamp(std::stod(v), 0.0, 1.0);
    } else if (a == "--duration") {
      const char* v = need_value(i, "--duration"); if (!v) return false;
      cfg.duration = std::max(0.1, std::stod(v));
    } else if (a == "--hz") {
      const char* v = need_value(i, "--hz"); if (!v) return false;
      cfg.hz = std::max(1.0, std::stod(v));
    } else if (a == "--right") {
      const char* v = need_value(i, "--right"); if (!v || !parse_q6(v, cfg.custom_right)) return false;
    } else if (a == "--left") {
      const char* v = need_value(i, "--left"); if (!v || !parse_q6(v, cfg.custom_left)) return false;
    } else {
      std::cerr << "Unknown option: " << a << "\n";
      usage(argv[0]);
      return false;
    }
  }
  if (cfg.hand != "left" && cfg.hand != "right" && cfg.hand != "both") {
    std::cerr << "--hand must be left/right/both\n";
    return false;
  }
  if (cfg.pose != "open" && cfg.pose != "close" && cfg.pose != "half" && cfg.pose != "custom") {
    std::cerr << "--pose must be open/close/half/custom\n";
    return false;
  }
  return true;
}

std::array<double, 6> pose_q(const Config& cfg, bool right) {
  if (cfg.pose == "open") {
    return {1, 1, 1, 1, 1, 1};
  }
  if (cfg.pose == "half") {
    return {0.5, 0.5, 0.5, 0.5, 0.5, 0.5};
  }
  if (cfg.pose == "close") {
    const double q = 1.0 - cfg.max_close;
    return {q, q, q, q, q, q};
  }
  return right ? cfg.custom_right : cfg.custom_left;
}

void fill(unitree_go::msg::dds_::MotorCmds_& msg,
          const std::array<double, 6>& right,
          const std::array<double, 6>& left,
          const std::string& hand) {
  msg.cmds().resize(12);
  for (size_t i = 0; i < 12; ++i) {
    msg.cmds()[i].mode(0);
    msg.cmds()[i].q(1.0f);
    msg.cmds()[i].dq(0.0f);
    msg.cmds()[i].tau(0.0f);
    msg.cmds()[i].kp(0.0f);
    msg.cmds()[i].kd(0.0f);
  }
  if (hand == "right" || hand == "both") {
    for (size_t i = 0; i < 6; ++i) msg.cmds()[i].q(static_cast<float>(right[i]));
  }
  if (hand == "left" || hand == "both") {
    for (size_t i = 0; i < 6; ++i) msg.cmds()[i + 6].q(static_cast<float>(left[i]));
  }
}

void print_q(const char* label, const std::array<double, 6>& q) {
  std::cout << label << " [";
  for (size_t i = 0; i < q.size(); ++i) {
    if (i) std::cout << ", ";
    std::cout << q[i];
  }
  std::cout << "]";
}

}  // namespace

int main(int argc, char** argv) {
  Config cfg;
  if (!parse_args(argc, argv, cfg)) return 1;
  std::signal(SIGINT, signal_handler);
  std::signal(SIGTERM, signal_handler);

  const auto right = pose_q(cfg, true);
  const auto left = pose_q(cfg, false);

  std::cout << "Inspire manual control\n"
            << "  iface: " << cfg.iface << "\n"
            << "  topic: " << cfg.topic << "\n"
            << "  hand: " << cfg.hand << "\n"
            << "  pose: " << cfg.pose << "\n"
            << "  max_close: " << cfg.max_close << "\n"
            << "  duration: " << cfg.duration << "s\n";
  print_q("  R", right); std::cout << "\n";
  print_q("  L", left); std::cout << "\n";

  unitree::robot::ChannelFactory::Instance()->Init(0, cfg.iface);
  unitree::robot::ChannelPublisherPtr<unitree_go::msg::dds_::MotorCmds_> pub(
      new unitree::robot::ChannelPublisher<unitree_go::msg::dds_::MotorCmds_>(cfg.topic));
  pub->InitChannel();

  unitree_go::msg::dds_::MotorCmds_ msg;
  fill(msg, right, left, cfg.hand);

  const auto period = std::chrono::duration<double>(1.0 / cfg.hz);
  const auto start = std::chrono::steady_clock::now();
  size_t count = 0;
  while (g_running) {
    pub->Write(msg);
    count++;
    if (std::chrono::steady_clock::now() - start >= std::chrono::duration<double>(cfg.duration)) {
      break;
    }
    std::this_thread::sleep_for(period);
  }
  std::cout << "Published " << count << " commands" << std::endl;
  return 0;
}

