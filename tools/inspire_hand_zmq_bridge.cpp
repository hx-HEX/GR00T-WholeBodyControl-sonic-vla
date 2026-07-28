#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstring>
#include <iostream>
#include <string>
#include <thread>

#include <msgpack.hpp>
#include <zmq.hpp>

#include <unitree/idl/go2/MotorCmds_.hpp>
#include <unitree/robot/channel/channel_factory.hpp>
#include <unitree/robot/channel/channel_publisher.hpp>

namespace {

std::atomic<bool> g_running{true};

void signal_handler(int) {
  g_running.store(false);
}

double clamp01(double x) {
  return std::clamp(x, 0.0, 1.0);
}

struct Config {
  std::string network_interface = "enp6s0";
  std::string zmq_endpoint = "tcp://localhost:5557";
  std::string zmq_topic = "g1_debug";
  std::string dds_topic = "rt/inspire/cmd";
  double max_close = 0.5;
  double publish_hz = 50.0;
  bool swap_hands = false;
  bool dry_run = false;
};

void usage(const char* argv0) {
  std::cout
      << "Usage: " << argv0 << " [options]\n"
      << "Options:\n"
      << "  --iface <name>          Unitree DDS network interface (default: enp6s0)\n"
      << "  --zmq-endpoint <addr>   SONIC debug ZMQ endpoint (default: tcp://localhost:5557)\n"
      << "  --zmq-topic <topic>     SONIC debug topic prefix (default: g1_debug)\n"
      << "  --dds-topic <topic>     Inspire command topic (default: rt/inspire/cmd)\n"
      << "  --max-close <0..1>      Limit max hand closure; 0=open only, 1=full (default: 0.5)\n"
      << "  --publish-hz <hz>       DDS publish rate (default: 50)\n"
      << "  --swap-hands            Swap left/right command mapping\n"
      << "  --dry-run               Decode and print without publishing DDS\n"
      << "  -h, --help              Show this help\n";
}

bool parse_args(int argc, char** argv, Config& cfg) {
  for (int i = 1; i < argc; ++i) {
    std::string a = argv[i];
    auto need_value = [&](const char* name) -> const char* {
      if (i + 1 >= argc) {
        std::cerr << "Missing value for " << name << "\n";
        return nullptr;
      }
      return argv[++i];
    };

    if (a == "-h" || a == "--help") {
      usage(argv[0]);
      return false;
    } else if (a == "--iface") {
      const char* v = need_value("--iface");
      if (!v) return false;
      cfg.network_interface = v;
    } else if (a == "--zmq-endpoint") {
      const char* v = need_value("--zmq-endpoint");
      if (!v) return false;
      cfg.zmq_endpoint = v;
    } else if (a == "--zmq-topic") {
      const char* v = need_value("--zmq-topic");
      if (!v) return false;
      cfg.zmq_topic = v;
    } else if (a == "--dds-topic") {
      const char* v = need_value("--dds-topic");
      if (!v) return false;
      cfg.dds_topic = v;
    } else if (a == "--max-close") {
      const char* v = need_value("--max-close");
      if (!v) return false;
      cfg.max_close = clamp01(std::stod(v));
    } else if (a == "--publish-hz") {
      const char* v = need_value("--publish-hz");
      if (!v) return false;
      cfg.publish_hz = std::max(1.0, std::stod(v));
    } else if (a == "--swap-hands") {
      cfg.swap_hands = true;
    } else if (a == "--dry-run") {
      cfg.dry_run = true;
    } else {
      std::cerr << "Unknown option: " << a << "\n";
      usage(argv[0]);
      return false;
    }
  }
  return true;
}

bool get_double_array(
    const msgpack::object& root,
    const std::string& key,
    std::array<double, 7>& out) {
  if (root.type != msgpack::type::MAP) {
    return false;
  }
  for (uint32_t i = 0; i < root.via.map.size; ++i) {
    const auto& kv = root.via.map.ptr[i];
    if (kv.key.type != msgpack::type::STR) {
      continue;
    }
    std::string k(kv.key.via.str.ptr, kv.key.via.str.size);
    if (k != key) {
      continue;
    }
    if (kv.val.type != msgpack::type::ARRAY || kv.val.via.array.size < out.size()) {
      return false;
    }
    for (size_t j = 0; j < out.size(); ++j) {
      kv.val.via.array.ptr[j].convert(out[j]);
    }
    return true;
  }
  return false;
}

// Convert SONIC/Dex3-style 7D joint targets to Inspire RH56 6D normalized q:
// Inspire order per Unitree dfx_inspire_service:
//   [pinky, ring, middle, index, thumb_bend, thumb_rotation]
// Inspire q convention:
//   0 = close, 1 = open
//
// SONIC hand targets are Dex3-style, open ~= 0 rad and closed is signed,
// depending on side. PICO in this repo only gives coarse trigger/grip-derived
// hand closures, so this bridge intentionally extracts robust closure ratios
// from magnitudes rather than relying on exact Dex3 joint semantics.
std::array<double, 6> dex3_to_inspire(const std::array<double, 7>& q7, double max_close) {
  const double thumb_rot_close = clamp01(std::abs(q7[0]) / 1.05);
  const double thumb_bend_close = clamp01(std::max(std::abs(q7[1]) / 0.75, std::abs(q7[2]) / 1.75));

  const double index_close = clamp01(std::max(std::abs(q7[3]) / 1.57, std::abs(q7[4]) / 1.75));
  const double middle_close = clamp01(std::max(std::abs(q7[5]) / 1.57, std::abs(q7[6]) / 1.75));

  // The PICO generator currently has no independent ring/pinky signal.
  // Mirror the available non-thumb closure so grasp demos still get a full-hand grasp.
  const double ring_close = middle_close;
  const double pinky_close = middle_close;

  auto inspire_q = [&](double close_ratio) {
    return 1.0 - clamp01(close_ratio) * max_close;
  };

  return {
      inspire_q(pinky_close),
      inspire_q(ring_close),
      inspire_q(middle_close),
      inspire_q(index_close),
      inspire_q(std::max(thumb_bend_close, std::max(index_close, middle_close) * 0.7)),
      inspire_q(thumb_rot_close),
  };
}

void fill_motor_cmds(
    unitree_go::msg::dds_::MotorCmds_& msg,
    const std::array<double, 6>& right,
    const std::array<double, 6>& left) {
  msg.cmds().resize(12);
  for (size_t i = 0; i < 6; ++i) {
    msg.cmds()[i].q(static_cast<float>(right[i]));
    msg.cmds()[i].dq(0.0f);
    msg.cmds()[i].tau(0.0f);
    msg.cmds()[i].kp(0.0f);
    msg.cmds()[i].kd(0.0f);
    msg.cmds()[i].mode(0);

    msg.cmds()[i + 6].q(static_cast<float>(left[i]));
    msg.cmds()[i + 6].dq(0.0f);
    msg.cmds()[i + 6].tau(0.0f);
    msg.cmds()[i + 6].kp(0.0f);
    msg.cmds()[i + 6].kd(0.0f);
    msg.cmds()[i + 6].mode(0);
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
  if (!parse_args(argc, argv, cfg)) {
    return 1;
  }

  std::signal(SIGINT, signal_handler);
  std::signal(SIGTERM, signal_handler);

  std::cout << "Inspire hand ZMQ bridge\n"
            << "  ZMQ: " << cfg.zmq_endpoint << " topic='" << cfg.zmq_topic << "'\n"
            << "  DDS: " << cfg.dds_topic << " iface='" << cfg.network_interface << "'\n"
            << "  max_close: " << cfg.max_close << "\n"
            << "  publish_hz: " << cfg.publish_hz << "\n"
            << "  swap_hands: " << (cfg.swap_hands ? "true" : "false") << "\n"
            << "  dry_run: " << (cfg.dry_run ? "true" : "false") << std::endl;

  unitree::robot::ChannelPublisherPtr<unitree_go::msg::dds_::MotorCmds_> publisher;
  if (!cfg.dry_run) {
    unitree::robot::ChannelFactory::Instance()->Init(0, cfg.network_interface);
    publisher.reset(new unitree::robot::ChannelPublisher<unitree_go::msg::dds_::MotorCmds_>(cfg.dds_topic));
    publisher->InitChannel();
  }

  zmq::context_t context(1);
  zmq::socket_t sub(context, zmq::socket_type::sub);
  sub.set(zmq::sockopt::subscribe, cfg.zmq_topic);
  sub.set(zmq::sockopt::conflate, 1);
  sub.set(zmq::sockopt::rcvtimeo, 200);
  sub.connect(cfg.zmq_endpoint);

  unitree_go::msg::dds_::MotorCmds_ cmd;
  const auto period = std::chrono::duration<double>(1.0 / cfg.publish_hz);
  auto last_pub = std::chrono::steady_clock::now() - period;
  auto last_print = std::chrono::steady_clock::now();
  uint64_t received = 0;
  uint64_t published = 0;

  while (g_running.load()) {
    zmq::message_t msg;
    zmq::recv_result_t ok;
    try {
      ok = sub.recv(msg, zmq::recv_flags::none);
    } catch (const zmq::error_t& e) {
      if (!g_running.load() || e.num() == EINTR || e.num() == ETERM) {
        break;
      }
      throw;
    }
    if (!ok) {
      continue;
    }
    if (msg.size() <= cfg.zmq_topic.size()) {
      continue;
    }

    const char* data = static_cast<const char*>(msg.data());
    const char* payload = data + cfg.zmq_topic.size();
    const size_t payload_size = msg.size() - cfg.zmq_topic.size();

    std::array<double, 7> left7{};
    std::array<double, 7> right7{};
    try {
      msgpack::object_handle oh = msgpack::unpack(payload, payload_size);
      const msgpack::object& root = oh.get();
      if (!get_double_array(root, "last_left_hand_action", left7) ||
          !get_double_array(root, "last_right_hand_action", right7)) {
        continue;
      }
    } catch (const std::exception& e) {
      std::cerr << "msgpack decode failed: " << e.what() << std::endl;
      continue;
    }

    received++;
    auto now = std::chrono::steady_clock::now();
    if (now - last_pub < period) {
      continue;
    }
    last_pub = now;

    auto left6 = dex3_to_inspire(left7, cfg.max_close);
    auto right6 = dex3_to_inspire(right7, cfg.max_close);
    if (cfg.swap_hands) {
      std::swap(left6, right6);
    }

    fill_motor_cmds(cmd, right6, left6);
    if (!cfg.dry_run) {
      publisher->Write(cmd);
    }
    published++;

    if (now - last_print > std::chrono::seconds(1)) {
      std::cout << "received=" << received << " published=" << published << " ";
      print_q("R", right6);
      std::cout << " ";
      print_q("L", left6);
      std::cout << std::endl;
      last_print = now;
    }
  }

  std::cout << "Stopping inspire hand bridge" << std::endl;
  return 0;
}
