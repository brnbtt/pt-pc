#pragma once

#include <string_view>

#include "engine/data/fox2.h"

namespace pt::game {

// Adapted from pt-ipad (buberlo), MIT.
inline const fox2::Property* FindScriptProperty(const fox2::DataSetFile& file,
                                                const fox2::Entity& entity, std::string_view key) {
    if (const auto* p = file.FindProperty(entity, key)) return p;
    // The US hallway f080 light conditions misspell this property, while their script uses modelData.
    if (entity.class_name == "GeoModuleCondition" && key == "modelData") {
        const auto* legacy = file.FindProperty(entity, "modleData");
        if (legacy && legacy->type == fox2::DataType::EntityLink &&
            legacy->container == fox2::Container::DynamicArray) return legacy;
    }
    return nullptr;
}

}
