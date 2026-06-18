<?php
declare(strict_types=1);

class EstimateService
{
    private GisService $gis;
    private MlService $ml;

    public function __construct()
    {
        $this->gis = new GisService();
        $this->ml = new MlService();
    }

    public function estimateLand(array $data): array
    {
        $lat = $data['Latitude']
            ?? $data['lat']
            ?? null;

        $lon = $data['Longitude']
            ?? $data['lng']
            ?? null;
        $area = (float)($data['area'] ?? 0);
        $frontage = isset($data['frontage']) ? (float)$data['frontage'] : null;

        $estimatedPrice = (int)round($area * 30000000);

        return [
            'ok' => true,
            'lat' => $lat,
            'lon' => $lon,
            'area' => $area,
            'frontage' => $frontage,
            'estimatedPrice' => $estimatedPrice,
            'note' => 'Ước lượng cơ bản từ diện tích.',
        ];
    }

public function predictPrice(array $data): array
{
    $lat = $data['Latitude'] ?? $data['lat'] ?? null;
    $lng = $data['Longitude'] ?? $data['lng'] ?? null;

    $feature = [
        "Latitude" => $lat,
        "Longitude" => $lng,
        "TenDuong" => $data["TenDuong"] ?? null,
        "Phuong" => $data["Phuong"] ?? null,
        "QuanHuyen" => $data["QuanHuyen"] ?? null,
        "ThanhPho" => $data["ThanhPho"] ?? null,
        "TinhThanh" => $data["TinhThanh"] ?? null,
        "QuocGia" => $data["QuocGia"] ?? "VN",
        "DiaChi" => $data["DiaChi"] ?? null,
        "nearbyRoads" => $data["nearbyRoads"] ?? [],
        "nearbyPlaces" => $data["nearbyPlaces"] ?? [],
    ];

    // Bổ sung thông tin từ GIS nếu thiếu
    if (
        $lat !== null &&
        $lng !== null &&
        (empty($feature['TenDuong']) || empty($feature['QuanHuyen']))
    ) {
        $nearest = $this->gis->findNearestRoadFeature(
            (float)$lat,
            (float)$lng
        );

        if ($nearest) {
            foreach ($nearest as $k => $v) {
                if (empty($feature[$k])) {
                    $feature[$k] = $v;
                }
            }
        }
    }

    // gọi FastAPI
    $result = $this->ml->predict($feature);

    // nếu ML lỗi
    if (!($result['ok'] ?? false)) {
        return [
            'ok' => false,
            'error' => $result['error'] ?? 'Predict failed'
        ];
    }

    // QUAN TRỌNG:
    // trả nguyên response của FastAPI
    return $result;
}

    public function searchAddress(string $keyword): array
    {
        return $this->gis->searchAddress($keyword);
    }
}