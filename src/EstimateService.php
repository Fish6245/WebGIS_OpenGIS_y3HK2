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
        "Latitude" => $data["Latitude"] ?? null,
        "Longitude" => $data["Longitude"] ?? null,
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

    $matchedFromFile = false;
    $matchedInfo = null;

    // GIS enrich
    if (
        $lat !== null &&
        $lng !== null &&
        (empty($feature['TenDuong']) || empty($feature['QuanHuyen']))
    ) {
        $nearest = $this->gis->findNearestRoadFeature((float)$lat, (float)$lng);

        if ($nearest) {
            foreach ($nearest as $k => $v) {
                if (empty($feature[$k])) {
                    $feature[$k] = $v;
                }
            }
        }
    }

    /**
     * ⚠️ QUAN TRỌNG:
     * Nếu bạn có logic match file ở ML layer thì phải trả về flag từ đó
     * Ví dụ ml->predict nên trả thêm matched_source
     */
    $prediction = $this->ml->predict($feature);

    // 👉 FIX: lấy flag từ ML response nếu có
    $matchedFromFile = $prediction['matched_source'] ?? false;
    $matchedInfo = $prediction['matched_info'] ?? null;

    return [
        'ok' => true,

        // giữ feature để debug
        'feature' => $feature,

        // prediction
        'prediction' => $prediction,

        // 🔥 FIX QUAN TRỌNG CHO FRONTEND
        'matched_source' => $matchedFromFile,
        'source_type' => $matchedFromFile ? 'file_match' : 'model_prediction',

        'matched_info' => $matchedInfo,
    ];
}

    public function searchAddress(string $keyword): array
    {
        return $this->gis->searchAddress($keyword);
    }
}