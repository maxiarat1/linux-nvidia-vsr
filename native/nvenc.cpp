// C ABI adapter over NVIDIA's MIT-licensed encoder helper classes.
// Pixel input stays in CUDA memory. No extra frame queue, lookahead or B
// frames.
#include "NvEncoder/NvEncoderCuda.h"
#include <memory>
#include <string>
simplelogger::Logger *logger = nullptr;
static thread_local std::string error;
struct Session {
  std::unique_ptr<NvEncoderCuda> encoder;
  CUcontext context;
  CUstream stream;
  int width, height;
  std::vector<NvEncOutputFrame> packets;
};
extern "C" {
const char *vsr_nvenc_error() { return error.c_str(); }
int vsr_nvenc_limits(void *context, void *stream, int *width, int *height) {
  try {
    NvEncoderCuda encoder((CUcontext)context, (CUstream)stream, 64, 64,
                          NV_ENC_BUFFER_FORMAT_NV12, 0);
    *width = encoder.GetCapabilityValue(NV_ENC_CODEC_AV1_GUID, NV_ENC_CAPS_WIDTH_MAX);
    *height = encoder.GetCapabilityValue(NV_ENC_CODEC_AV1_GUID, NV_ENC_CAPS_HEIGHT_MAX);
    return 0;
  } catch (const std::exception &e) {
    error = e.what();
    return -1;
  }
}
void *vsr_nvenc_create(int width, int height, void *context, void *stream,
                       int qp) {
  try {
    auto s = std::make_unique<Session>();
    s->context = (CUcontext)context;
    s->stream = (CUstream)stream;
    s->width = width;
    s->height = height;
    s->encoder = std::make_unique<NvEncoderCuda>(
        s->context, s->stream, width, height, NV_ENC_BUFFER_FORMAT_NV12, 0,
        false, false, false, true);
    NV_ENC_INITIALIZE_PARAMS p = {NV_ENC_INITIALIZE_PARAMS_VER};
    NV_ENC_CONFIG c = {NV_ENC_CONFIG_VER};
    p.encodeConfig = &c;
    s->encoder->CreateDefaultEncoderParams(
        &p, NV_ENC_CODEC_AV1_GUID, NV_ENC_PRESET_P1_GUID,
        NV_ENC_TUNING_INFO_ULTRA_LOW_LATENCY);
    p.frameRateNum = 60;
    p.frameRateDen = 1;
    c.gopLength = 1;
    c.frameIntervalP = 1;
    c.rcParams.rateControlMode = NV_ENC_PARAMS_RC_CONSTQP;
    c.rcParams.constQP = {static_cast<uint32_t>(qp), static_cast<uint32_t>(qp),
                          static_cast<uint32_t>(qp)};
    c.rcParams.enableLookahead = 0;
    c.rcParams.zeroReorderDelay = 1;
    c.encodeCodecConfig.av1Config.repeatSeqHdr = 1;
    c.encodeCodecConfig.av1Config.idrPeriod = 1;
    c.encodeCodecConfig.av1Config.colorPrimaries =
        NV_ENC_VUI_COLOR_PRIMARIES_BT709;
    c.encodeCodecConfig.av1Config.transferCharacteristics =
        NV_ENC_VUI_TRANSFER_CHARACTERISTIC_BT709;
    c.encodeCodecConfig.av1Config.matrixCoefficients =
        NV_ENC_VUI_MATRIX_COEFFS_BT709;
    s->encoder->CreateEncoder(&p);
    return s.release();
  } catch (const std::exception &e) {
    error = e.what();
    return nullptr;
  }
}
int vsr_nvenc_encode(void *handle, void *pixels, const unsigned char **bytes,
                     size_t *length) {
  try {
    auto &s = *static_cast<Session *>(handle);
    s.packets.clear();
    auto frame = s.encoder->GetNextInputFrame();
    uint32_t offsets[] = {static_cast<uint32_t>(s.width * s.height), 0};
    NvEncoderCuda::CopyToDeviceFrame(
        s.context, pixels, s.width, (CUdeviceptr)frame->inputPtr, frame->pitch,
        s.width, s.height, CU_MEMORYTYPE_DEVICE, frame->bufferFormat,
        frame->chromaOffsets, frame->numChromaPlanes, false, s.stream, offsets);
    NV_ENC_PIC_PARAMS pic = {NV_ENC_PIC_PARAMS_VER};
    pic.encodePicFlags =
        NV_ENC_PIC_FLAG_FORCEIDR | NV_ENC_PIC_FLAG_OUTPUT_SPSPPS;
    s.encoder->EncodeFrame(s.packets, &pic);
    if (s.packets.size() != 1)
      throw std::runtime_error(
          "NVENC must return exactly one packet per frame without buffering");
    *bytes = s.packets[0].frame.data();
    *length = s.packets[0].frame.size();
    return 0;
  } catch (const std::exception &e) {
    error = e.what();
    return -1;
  }
}
int vsr_nvenc_destroy(void *handle) {
  try {
    auto s = std::unique_ptr<Session>(static_cast<Session *>(handle));
    s->encoder->EndEncode(s->packets);
    return 0;
  } catch (const std::exception &e) {
    error = e.what();
    return -1;
  }
}
}
